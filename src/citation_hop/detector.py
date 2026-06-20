"""Detect whether a chunk of selected text looks like a citation,
and if so, which format it is in.

Public API:
    Format                  = Literal["bibtex", "ris", "plain"]
    detect_format()         -> Format | None
    is_likely_citation(text)-> bool
    detect_in_text_citation()-> dict | None
    is_in_text_citation()   -> bool

Heuristic gate
--------------
The gate prevents the app from spamming Crossref every time the user
selects a sentence in a paper. A piece of text is treated as a citation
candidate only if it (a) is between 40 and 2000 characters, and
(b) carries at least two of the well-known citation signals
(publication year, volume(issue), DOI, page/volume keyword, et al.,
or a Surname, I. author pattern).

In-text citations
-----------------
A separate detector recognises the much shorter in-text citation
patterns — ``(Smith, 2020)``, ``Smith (2020)``, ``(Smith et al., 2020)``,
``(Smith & Jones, 2020)``, with optional disambig letter
(``Smith, 2020a``). These are usually 8–40 characters, well below the
40-character floor of the full-reference gate, so they need their own
detector.  When matched, the lookup pipeline skips Crossref entirely
and routes the parsed ``author + year`` straight to the search engines.
This is both faster and more accurate: Crossref's ``query.bibliographic``
on a 15-character author-year string is mostly noise.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from .extractor import _DOI_RE

Format = Literal["bibtex", "ris", "plain"]

_MIN_LEN = 40
_MAX_LEN = 2000

# --- BibTeX / RIS fingerprints --------------------------------------------

_BIBTEX_RE = re.compile(
    r"@\w+\s*\{[^}]*?(?:author|title|doi|year|journal)\s*=",
    re.IGNORECASE | re.DOTALL,
)

_RIS_RE = re.compile(
    r"^TY\s*-\s*",
    re.MULTILINE,
)

# --- "Plain text" (APA/MLA/Chicago) signal patterns -----------------------

# (1999), (1999a), bare 4-digit year inside text
_YEAR_RE = re.compile(r"\((?:19|20)\d{2}[a-z]?\)|\b(?:19|20)\d{2}\b")
_VOL_ISSUE_RE = re.compile(r"\b\d+\s*\(\s*\d+\s*\)")
_PAGE_KEYWORDS_RE = re.compile(
    r"\b(?:pp?\.|vol\.|no\.|pages?)\b", re.IGNORECASE
)
_ET_AL_RE = re.compile(r"\bet\s+al\.?", re.IGNORECASE)
# APA surname pattern matches:
#   "Heidari, A."         — surname, comma, single-letter initial
#   "Sinclair, John McH." — surname, comma, multi-letter given name + cap
#   "van der Berg, A."    — particle surname (just leading capital letter)
# The required suffix after the comma is one or more *capitalised* tokens
# (with optional spaces).  Each token is either a single letter (initial)
# or a full word.  End punctuation (period, comma, &) is not required —
# Heidari's "A." has the period, but "Sinclair, John McH" does not.
_SURNAME_INITIAL_RE = re.compile(
    r"\b[A-Z][a-zA-Z\-']{1,},\s+"
    r"(?:[A-Z]\.?(?:\s+[A-Z][a-zA-Z]+)*"
    r"|[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)"
)

# --- In-text citation patterns --------------------------------------------
#
# Parenthetical:  (Smith, 2020)  (Smith & Jones, 2020)
#                 (Smith et al., 2020)  (Smith et al., 2020a)
# Narrative:      Smith (2020)  Smith and Jones (2020)
#                 Smith et al. (2020)
#
# Author body matches:
#   - single capitalised surname
#   - surname + (& | and) + capitalised surname
#   - surname + et al.
#   - surname + et al. + (& | and) + capitalised surname   (APA 21+ authors)
# In all cases the author body is followed by either:
#   - a comma and the year, all wrapped in ( ... )   — parenthetical
#   - the year wrapped in ( ... )                    — narrative

_IN_TEXT_AUTHOR_BODY = (
    r"[A-Z][a-zA-Z\-\']+"                                # surname
    r"(?:"                                               # optional tails:
    r"\s+(?:&|and)\s+[A-Z][a-zA-Z\-\']+"                 #   & coauthor
    r"|\s+et\s+al\.?"                                    #   et al.
    r")?"
    r"(?:"                                               # more coauthors after et al.
    r"\s+(?:&|and)\s+[A-Z][a-zA-Z\-\']+"
    r")*"
)

_IN_TEXT_PAREN_RE = re.compile(
    r"^\s*\(\s*"
    + _IN_TEXT_AUTHOR_BODY +
    r"\s*,?\s*"                                # comma optional (Chicago/Harvard)
    r"(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?"
    r"\s*\)\s*$",
    re.IGNORECASE,
)

_IN_TEXT_NARRATIVE_RE = re.compile(
    r"^\s*"
    + _IN_TEXT_AUTHOR_BODY +
    r"\s*\(\s*"
    r"(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?"
    r"\s*\)\s*$",
    re.IGNORECASE,
)

# Multiple parenthetical citations separated by semicolons:
#   (Smith, 2020; Jones, 2021)
#   (Sinclair 1966; Halliday 1961)            ← no comma between author and year
#   (Smith, 2020a; Jones, 2021b)
# Each inner cite is: Surname [optional ,] YEAR[a-z]
_IN_TEXT_MULTI_PAREN_RE = re.compile(
    r"^\s*\(\s*"
    r"[A-Z][a-zA-Z\-']+(?:\s+et\s+al\.?)?"
    r"\s*,?\s*"
    r"(?:19|20)\d{2}[a-z]?"
    r"(?:\s*;\s*[A-Z][a-zA-Z\-']+(?:\s+et\s+al\.?)?\s*,?\s*(?:19|20)\d{2}[a-z]?)+"
    r"\s*\)\s*$",
    re.IGNORECASE,
)

# Upper bound on in-text citation length: parenthetical form with
# "Surname et al." + year + suffix is ~32 chars; a worst-case
# "Surname, A. B., & Surname, C. D." (APA 21+ authors) can hit ~50.
# We use 80 to leave headroom for non-breaking spaces, etc.
# Upper bound extended to 200 to accommodate parenthetical multi-cites
# like ``(Sinclair 1966; Halliday 1961; Smith et al. 2020)``.
_IN_TEXT_MAX_LEN = 200


def detect_format(text: str) -> Optional[Format]:
    """Return which citation family *text* belongs to, or None.

    Order of checks: BibTeX → RIS → plain (APA/MLA/Chicago).
    """
    if not text:
        return None

    if _BIBTEX_RE.search(text):
        return "bibtex"
    if _RIS_RE.search(text):
        return "ris"
    return "plain"


def is_likely_citation(text: str) -> bool:
    """Return True iff *text* passes the citation heuristic gate.

    The gate requires:
      - length in [40, 2000]
      - ≥ 2 of the 6 citation signals listed at module top
    """
    if not text:
        return False
    stripped = text.strip()
    if not (_MIN_LEN <= len(stripped) <= _MAX_LEN):
        return False

    signals = sum(
        bool(p.search(stripped))
        for p in (
            _YEAR_RE,
            _VOL_ISSUE_RE,
            _DOI_RE,
            _PAGE_KEYWORDS_RE,
            _ET_AL_RE,
            _SURNAME_INITIAL_RE,
        )
    )
    return signals >= 2


# ---------------------------------------------------------------------------
# In-text citation detector
# ---------------------------------------------------------------------------

def detect_in_text_citation(text: str) -> Optional[dict]:
    """Detect an in-text citation like ``(Smith, 2020)`` or ``Smith et al. (2020)``.

    Returns a dict on hit, ``None`` otherwise::

        {
            "kind":   "author_year",
            "author": "Smith",
            "year":   "2020",      # may include disambig: "2020a"
        }

    Recognised forms (case-insensitive on the separators):

    * Parenthetical — ``(Author, YYYY[a])``
    * Narrative     — ``Author (YYYY[a])``

    Author body allows ``&`` / ``and`` and ``et al.`` (with an optional
    trailing co-author after ``et al.``).  A 4-digit year (1900-2099)
    is required; the optional ``[a-z]`` suffix handles APA-style
    same-year disambiguation (``Smith, 2020a``).

    The text must fit within :data:`_IN_TEXT_MAX_LEN` characters
    (default 80) to avoid catching entire paragraphs that happen to
    start with a surname.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > _IN_TEXT_MAX_LEN:
        return None

    # Multi-cite parenthetical form: (Smith, 2020; Jones, 2021).
    # We detect this first because the multi-paren regex is more
    # specific than the single-paren one.  For multi-cite we don't
    # return a single author/year — we return a list of cite dicts
    # under the "cites" key so the caller can fall through to the
    # full-reference detector (since a single in-text reference may
    # resolve to multiple DOIs).
    multi = _IN_TEXT_MULTI_PAREN_RE.match(stripped)
    if multi:
        # Extract each (Author, Year) pair from the matched text.
        body = stripped[1:-1]  # strip outer parens
        cites = []
        for part in body.split(";"):
            part = part.strip()
            if not part:
                continue
            # Author = leading run of letters/spaces, year = 4 digits
            mm = re.match(
                r"(?P<author>[A-Z][a-zA-Z\-']+(?:\s+et\s+al\.?)?)"
                r"\s*,?\s*"
                r"(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?",
                part,
                re.IGNORECASE,
            )
            if mm:
                cites.append({
                    "author": mm.group("author").strip(),
                    "year": mm.group("year") + (mm.group("suffix") or ""),
                })
        if cites:
            return {
                "kind": "author_year_multi",
                "cites": cites,
            }

    m = _IN_TEXT_PAREN_RE.match(stripped) or _IN_TEXT_NARRATIVE_RE.match(stripped)
    if not m:
        return None

    year = m.group("year")
    suffix = m.group("suffix") or ""
    full = m.group(0).strip()
    # Locate the year position inside the matched text and slice off
    # everything after it (and any closing paren).  What remains is the
    # author body, possibly with a leading "(" or trailing ","/"(".
    year_idx = full.find(year)
    author_body = full[:year_idx].rstrip()
    # Strip the *opening* paren of the year group, which is always the
    # last character before the year for the narrative form, and never
    # present for the parenthetical form (it's the leading char there).
    while author_body and author_body[-1] in "(,":
        author_body = author_body[:-1].rstrip()
    if author_body.startswith("("):
        author_body = author_body[1:].rstrip()
    if not author_body:
        return None
    # Normalise " and " to " & " for compactness in URLs / display.
    author_body = re.sub(r"\s+and\s+", " & ", author_body, flags=re.IGNORECASE)

    return {
        "kind": "author_year",
        "author": author_body,
        "year": year + suffix,
    }


def is_in_text_citation(text: str) -> bool:
    """Return True iff *text* matches an in-text citation pattern."""
    return detect_in_text_citation(text) is not None


__all__ = [
    "Format",
    "detect_format",
    "is_likely_citation",
    "detect_in_text_citation",
    "is_in_text_citation",
]
