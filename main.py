"""Core lookup logic for citationHop.

This module deliberately avoids importing rumps / pynput so that the
pure ``lookup()`` function can be unit-tested on any platform.
"""

from __future__ import annotations

from typing import Optional

from .detector import is_likely_citation
from .parser import parse_fields
from .resolver import build_scholar_url, doi_url, resolve_via_crossref


def lookup(
    text: str,
    *,
    mailto: Optional[str] = None,
    threshold: Optional[float] = None,  # noqa: ARG001 (kept for future)
) -> dict:
    """Resolve *text* to either a DOI + URL, or a Google Scholar URL.

    Returns a dict with:
        status: "doi" | "scholar" | "empty" | "not_citation"
        url:    the URL to open
        doi:    the resolved DOI, or None
        title:  the detected title (best effort)
    """
    if not text or not text.strip():
        return {"status": "empty", "url": "", "doi": None, "title": None}

    if not is_likely_citation(text):
        return {
            "status": "not_citation",
            "url": "",
            "doi": None,
            "title": None,
        }

    fields = parse_fields(text)
    doi = fields.get("doi")

    if not doi:
        doi = resolve_via_crossref(text, mailto=mailto)

    if doi:
        return {
            "status": "doi",
            "url": doi_url(doi),
            "doi": doi,
            "title": fields.get("title"),
        }

    return {
        "status": "scholar",
        "url": build_scholar_url(fields),
        "doi": None,
        "title": fields.get("title"),
    }


__all__ = ["lookup"]
