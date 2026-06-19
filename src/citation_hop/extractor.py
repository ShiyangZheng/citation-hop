"""DOI extraction from arbitrary text.

Exposes a single public function:

    extract_doi(text: str) -> Optional[str]

It recognises three common forms:
  1. Bare DOI in a citation:  "10.1038/nature12373"
  2. doi: prefix:             "doi:10.1038/nature12373"
  3. URL form:                "https://doi.org/10.1038/nature12373"

Returns the *normalised* bare DOI (no prefix, no trailing punctuation).
"""

from __future__ import annotations

import re
from typing import Optional

# Crossref DOI syntax: 10.NNNN/anything
# The DOI suffix can contain virtually any printable character except
# whitespace and quote marks (quotes are never part of a DOI). Real-world
# examples include `(SICI)1097-0118(...)14:1<23::AID-JEO450>3.0.CO;2-7`.
_DOI_RE = re.compile(
    r"10\.\d{4,9}/[^\s\"']+",
    re.IGNORECASE,
)

# Trailing punctuation we strip from a matched DOI before validating.
# A real DOI suffix is very unlikely to end in any of these.
_TRAILING_PUNCT = ".,);]}\"'"


def _strip_prefix(text: str) -> str:
    """Remove common DOI prefixes (https://doi.org/, doi:, DOI:)."""
    # URL form
    text = re.sub(
        r"https?://(?:dx\.)?doi\.org/",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Inline prefix (with or without space)
    text = re.sub(r"^\s*doi\s*:\s*", "", text, flags=re.IGNORECASE)
    return text


def _strip_trailing(text: str) -> str:
    """Trim characters that are not part of a real DOI suffix.

    Crossref DOI suffixes are intentionally permissive, but in citations
    they are commonly followed by a sentence-ending punctuation mark
    (e.g. "10.1234/abc." or "10.1234/abc),"). We strip only the most
    common offenders; if the stripping changes the DOI shape, we abort
    and return the original.
    """
    original = text
    while text and text[-1] in _TRAILING_PUNCT:
        text = text[:-1]
    return text or original


def extract_doi(text: str) -> Optional[str]:
    """Return the first DOI found in *text*, normalised.

    Returns None if no DOI is present.
    """
    if not text:
        return None

    candidate = _strip_prefix(text)
    match = _DOI_RE.search(candidate)
    if not match:
        return None

    doi = match.group(0)
    doi = _strip_trailing(doi)
    # Re-validate after normalisation — must still look like a DOI.
    if not _DOI_RE.fullmatch(doi):
        return match.group(0).rstrip(".,);]}\"'")
    return doi
