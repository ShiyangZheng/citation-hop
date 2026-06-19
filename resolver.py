"""DOI resolution via the Crossref API, with title-similarity scoring.

Public API:
    resolve_via_crossref(text, *, mailto=None, timeout=8.0) -> Optional[str]
    build_scholar_url(fields) -> str

The Crossref polite pool
------------------------
Crossref gives higher rate limits to requests whose User-Agent includes
a contact email. We do this by default; it is overridable via *mailto*
for downstream users.
"""

from __future__ import annotations

import difflib
import re
import urllib.parse
from typing import Optional

import requests

CROSSREF_API = "https://api.crossref.org/works"
DEFAULT_MAILTO = "syz@shiyangzheng.top"
USER_AGENT = "citationHop/1.0 (mailto:{mailto})"
SIMILARITY_THRESHOLD = 0.85

# Crossref returns a "title" list (sometimes 0, 1, or N entries) and
# a "subtitle". We concatenate them for matching.
_WS_RE = re.compile(r"\s+")


def _normalise_title(t: str) -> str:
    return _WS_RE.sub(" ", t).strip().lower()


def _best_title_match(query_text: str, candidate_title: str) -> float:
    """Return a 0..1 similarity between *query_text* and the candidate
    title.

    The user's selected text is usually a full APA / MLA / Chicago
    reference (author + year + title + journal + volume + pages),
    whereas the Crossref result title is just the paper title. A plain
    SequenceMatcher ratio would be depressed by all the extra metadata.
    We therefore:
      1. Score 1.0 if the normalised title is a substring of (or vice
         versa with) the normalised query — this is the common case.
      2. Otherwise fall back to SequenceMatcher ratio.
    """
    q = _normalise_title(query_text)
    c = _normalise_title(candidate_title)
    if not q or not c:
        return 0.0
    if c in q or q in c:
        # The shorter string is wholly contained in the longer one. This
        # is the strongest signal of "same paper".
        return 1.0
    return difflib.SequenceMatcher(None, q, c).ratio()


def _user_agent(mailto: Optional[str]) -> str:
    return USER_AGENT.format(mailto=(mailto or DEFAULT_MAILTO))


def resolve_via_crossref(
    text: str,
    *,
    mailto: Optional[str] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Try to find a DOI for *text* via the Crossref bibliographic search.

    Returns the DOI string on a confident match, or None on miss / error.
    """
    if not text or not text.strip():
        return None

    sess = session or requests.Session()
    headers = {"User-Agent": _user_agent(mailto), "Accept": "application/json"}
    params = {
        "query.bibliographic": text,
        "rows": 3,
    }

    try:
        resp = sess.get(
            CROSSREF_API,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None

    items = (payload.get("message") or {}).get("items") or []
    best_score = 0.0
    best_doi: Optional[str] = None

    for item in items:
        doi = item.get("DOI")
        if not doi:
            continue
        title_parts = list(item.get("title") or [])
        subtitle_parts = list(item.get("subtitle") or [])
        candidate_title = " ".join(title_parts + subtitle_parts).strip()
        if not candidate_title:
            continue
        score = _best_title_match(text, candidate_title)
        if score > best_score:
            best_score = score
            best_doi = doi

    if best_doi and best_score >= SIMILARITY_THRESHOLD:
        return best_doi
    return None


def build_scholar_url(fields: dict) -> str:
    """Build a Google Scholar search URL from the parsed fields.

    We use title + first author surname + year when available. The query
    is intentionally not too specific — Scholar is the *fallback*, and a
    too-narrow query can give zero results.
    """
    parts: list[str] = []

    title = (fields.get("title") or "").strip()
    if title:
        parts.append(f'"{title}"')

    author = (fields.get("author") or "").strip()
    if author:
        # Take the first surname (everything before the first comma, or
        # the whole token for single-name authors).
        first_surname = re.split(r"[,\s]+", author, maxsplit=1)[0]
        if first_surname:
            parts.append(f'author:"{first_surname}"')

    year = (fields.get("year") or "").strip()
    if year:
        parts.append(year)

    query = " ".join(parts) if parts else (fields.get("title") or author or "")
    return "https://scholar.google.com/scholar?q=" + urllib.parse.quote(query)


def doi_url(doi: str) -> str:
    """Return the canonical resolver URL for a DOI."""
    return "https://doi.org/" + urllib.parse.quote(doi, safe="/")


__all__ = [
    "resolve_via_crossref",
    "build_scholar_url",
    "doi_url",
    "SIMILARITY_THRESHOLD",
]
