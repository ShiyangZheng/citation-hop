"""Core lookup logic for citationHop.

This module deliberately avoids importing pystray / platform_utils so
that ``lookup()`` can be unit-tested on any platform (including CI).

In v1.1+, ``lookup()`` is engine-driven: it takes the full engine list
(user-configured) and walks the three stages
(``doi_resolver`` -> ``doi_url`` or ``search_url``).
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .detector import is_likely_citation
from .engines import Engine, STAGE_DOI_RESOLVER, by_stage
from .parser import parse_fields
from .resolver import (
    build_doi_url,
    build_search_url,
    doi_url,
    resolve_doi,
)


def lookup(
    text: str,
    *,
    engines: Iterable[Engine],
    mailto: Optional[str] = None,
) -> dict:
    """Resolve *text* to a URL using the supplied *engines*.

    Stages:

    1. Gate: is this even a citation?
    2. Parse fields (BibTeX / RIS / plain).
    3. If we don't yet have a DOI, try enabled ``doi_resolver`` engines.
    4. If we have a DOI, render the first enabled ``doi_url`` engine.
    5. Otherwise, render the first enabled ``search_url`` engine.

    Returns a dict with:

        status:        "doi" | "search" | "empty" | "not_citation"
        url:           the URL to open
        doi:           the resolved DOI, or None
        title:         the parsed title (best effort)
        engine_used:   id of the engine whose URL was returned
    """
    if not text or not text.strip():
        return _empty_result()

    if not is_likely_citation(text):
        return _not_citation_result()

    engine_list: List[Engine] = list(engines)
    fields = parse_fields(text)
    doi = fields.get("doi")

    # Stage 3: ask a doi_resolver engine for a DOI if we don't have one.
    if not doi:
        doi = resolve_doi(text, engine_list, mailto=mailto)

    # Stage 4: if we have a DOI, prefer a doi_url engine.
    if doi:
        url = build_doi_url(doi, engine_list, mailto=mailto)
        if url is None:
            # No doi_url engine enabled — fall back to the canonical URL.
            url = doi_url(doi)
            engine_used = "doi_org"
        else:
            engine_used = _first_enabled_id(engine_list, "doi_url") or "doi_url"
        return {
            "status": "doi",
            "url": url,
            "doi": doi,
            "title": fields.get("title"),
            "engine_used": engine_used,
        }

    # Stage 5: no DOI — build a search URL.
    url = build_search_url(text, fields, engine_list, mailto=mailto)
    if url is None:
        # Last-ditch: use the canonical Google Scholar URL even if the
        # user has disabled every search engine.  We still want *some*
        # result so the user knows the hotkey fired.
        url = (
            "https://scholar.google.com/scholar?q="
            + _safe_quote(text)
        )
        engine_used = "scholar_fallback"
    else:
        engine_used = _first_enabled_id(engine_list, "search_url") or "search_url"

    return {
        "status": "search",
        "url": url,
        "doi": None,
        "title": fields.get("title"),
        "engine_used": engine_used,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_result() -> dict:
    return {
        "status": "empty",
        "url": "",
        "doi": None,
        "title": None,
        "engine_used": None,
    }


def _not_citation_result() -> dict:
    return {
        "status": "not_citation",
        "url": "",
        "doi": None,
        "title": None,
        "engine_used": None,
    }


def _first_enabled_id(engines: List[Engine], stage: str) -> Optional[str]:
    for e in by_stage(engines, stage):
        return e.id
    return None


def _safe_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text or "")


__all__ = ["lookup"]
