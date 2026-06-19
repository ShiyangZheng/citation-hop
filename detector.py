"""Detect whether a chunk of selected text looks like a citation,
and if so, which format it is in.

Public API:
    Format          = Literal["bibtex", "ris", "plain"]
    detect_format() -> Format | None
    is_likely_citation(text) -> bool

Heuristic gate
--------------
The gate prevents the app from spamming Crossref every time the user
selects a sentence in a paper. A piece of text is treated as a citation
candidate only if it (a) is between 40 and 2000 characters, and
(b) carries at least two of the well-known citation signals
(publication year, volume(issue), DOI, page/volume keyword, et al.,
or a Surname, I. author pattern).
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
_SURNAME_INITIAL_RE = re.compile(
    r"\b[A-Z][a-zA-Z\-']{1,},\s*[A-Z]\."
)


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
