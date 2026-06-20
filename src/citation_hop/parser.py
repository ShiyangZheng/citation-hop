"""Field-level extraction for each supported citation format.

The output is the same dict regardless of input format:

    {
        "title":  str | None,
        "author": str | None,    # "Surname1, Surname2 and Surname3" or similar
        "year":   str | None,    # four digits as a string
        "doi":    str | None,    # already normalised by extractor.extract_doi
    }

Even if a DOI is present, the title is still extracted (so we can show
the user what we matched).
"""

from __future__ import annotations

import re
from typing import Optional

from .detector import Format, detect_format
from .extractor import extract_doi

# --- BibTeX ----------------------------------------------------------------

_BIBTEX_FIELD_RE = re.compile(
    r"(?P<key>author|title|journal|year|doi|volume|number|pages)\s*=\s*"
    r"(?P<quote>[\"\{\[])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)

# --- RIS -------------------------------------------------------------------

# RIS uses two-letter tags followed by "  - " then the value.
_RIS_FIELD_RE = re.compile(
    r"^(?P<tag>[A-Z][A-Z0-9])\s*-\s*(?P<value>.*?)\s*$",
    re.MULTILINE,
)

# --- Plain text (APA / MLA / Chicago) -------------------------------------

_YEAR_PAREN_RE = re.compile(r"\(((?:19|20)\d{2})[a-z]?\)")
_YEAR_BARE_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# APA reference shape: "Authors. (YYYY). Title. Journal, vol(issue), pages."
# We isolate the title as the segment between the year-paren and the
# next ". " followed by a capitalised word (which is almost always the
# start of the journal / container title).  This is what makes the
# Scholar fallback URL meaningful: with title=None, the URL reduces to
# "author + year", which often returns the wrong paper on Scholar.
_APA_TITLE_AFTER_YEAR_RE = re.compile(
    # ". (YYYY). " opening
    r"\.\s*\((?:19|20)\d{2}[a-z]?\)\.\s*"
    r"(?P<title>.+?)"                          # title (lazy)
    # terminator: ". " followed by a capitalised word, or end of string
    r"(?:\.\s+(?=[A-Z])|$)"
)


def _parse_bibtex(text: str) -> dict:
    fields: dict = {
        "title": None,
        "author": None,
        "year": None,
        "doi": None,
    }

    authors: list[str] = []
    for m in _BIBTEX_FIELD_RE.finditer(text):
        key = m.group("key").lower()
        value = m.group("value").strip()
        if key == "title":
            fields["title"] = value
        elif key == "author":
            authors.append(value)
        elif key == "year":
            fields["year"] = value
        elif key == "doi":
            fields["doi"] = value  # already raw; will be normalised later
    if authors:
        fields["author"] = " and ".join(authors)
    return fields


def _parse_ris(text: str) -> dict:
    fields: dict = {
        "title": None,
        "author": None,
        "year": None,
        "doi": None,
    }
    authors: list[str] = []
    for m in _RIS_FIELD_RE.finditer(text):
        tag = m.group("tag").upper()
        value = m.group("value").strip()
        if tag == "TI":
            fields["title"] = value
        elif tag == "T1" and not fields["title"]:  # some dialects
            fields["title"] = value
        elif tag == "AU" or tag == "A1":
            authors.append(value)
        elif tag == "PY" or tag == "Y1":
            # RIS year is often "YYYY/MM/DD///"
            fields["year"] = value.split("/")[0].strip()
        elif tag == "DO":
            fields["doi"] = value
    if authors:
        fields["author"] = ", ".join(authors)
    return fields


def _parse_plain(text: str) -> dict:
    fields: dict = {
        "title": None,
        "author": None,
        "year": None,
        "doi": None,
    }

    # Year — try parenthesised first (APA style), then bare.
    m = _YEAR_PAREN_RE.search(text)
    if m:
        fields["year"] = m.group(1)
    else:
        m = _YEAR_BARE_RE.search(text)
        if m:
            fields["year"] = m.group(1)

    # Author — anything before the first parenthetical year, or the first
    # sentence. We deliberately keep this heuristic; APA/MLA are
    # inconsistent and Crossref will do the heavy matching anyway.
    if "(" in text:
        head = text.split("(", 1)[0].strip().rstrip(".,")
    else:
        head = text.split(".", 1)[0].strip()
    # Trim very long heads — they probably captured more than the author.
    if head and len(head) <= 200:
        fields["author"] = head

    # Title — try to isolate the APA title segment ("Authors. (Year).
    # TITLE. Journal...").  Without this, the Scholar fallback URL is
    # only `?q=author+year`, which often maps to the wrong paper on
    # Scholar — the user perceives it as "the tool always opens the
    # same paper".  We only attempt this for the APA shape (year
    # wrapped in parens); MLA / Chicago don't follow the same template.
    m = _APA_TITLE_AFTER_YEAR_RE.search(text)
    if m:
        title = m.group("title").strip().rstrip(",")
        # The lazy match may swallow a trailing ". " if the title ends
        # at end-of-string without a following capitalised word.  Trim.
        title = title.rstrip(".").strip()
        if title and len(title) <= 400:
            fields["title"] = title

    return fields


def parse_fields(text: str) -> dict:
    """Detect the format and extract fields. Always returns the dict shape."""
    fmt = detect_format(text) or "plain"

    if fmt == "bibtex":
        fields = _parse_bibtex(text)
    elif fmt == "ris":
        fields = _parse_ris(text)
    else:
        fields = _parse_plain(text)

    # Normalise DOI in all formats (BibTeX / RIS may have bare DOIs).
    if fields.get("doi"):
        normalised = extract_doi(fields["doi"])
        if normalised:
            fields["doi"] = normalised
        else:
            # Looks like a DOI field but doesn't match the pattern; keep
            # the raw value, the resolver can still try.
            fields["doi"] = fields["doi"].strip().rstrip(".,);")
    else:
        # Try to fish a DOI out of the raw text (handles APA / plain).
        fields["doi"] = extract_doi(text)

    return fields


__all__ = ["parse_fields", "Format"]
