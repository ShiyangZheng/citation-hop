"""Core lookup logic for citationHop.

This module deliberately avoids importing pystray / platform_utils so
that ``lookup()`` can be unit-tested on any platform (including CI).

In v1.1+, ``lookup()`` is engine-driven: it takes the full engine list
(user-configured) and walks the three stages
(``doi_resolver`` -> ``doi_url`` or ``search_url``).

Lookup pipeline
---------------
1. **In-text short-circuit** — if the selection matches an in-text
   citation pattern like ``(Smith, 2020)`` or ``Smith et al. (2020)``,
   we skip Crossref entirely (an 8-40 character author-year string is
   mostly noise to Crossref's ``query.bibliographic``) and route the
   parsed ``author + year`` straight to the first enabled
   ``search_url`` engine.  Result status: ``in_text``.
2. **Gate** — is this even a citation (full reference)?
3. **Parse fields** (BibTeX / RIS / plain).
4. **Resolve DOI** — if we don't have a local DOI, ask the first
   enabled ``doi_resolver`` engine (Crossref by default).
5. **Render DOI URL** — if we now have a DOI, render the first
   enabled ``doi_url`` engine.  Result status: ``doi``.
6. **Render search URL** — otherwise, render the first enabled
   ``search_url`` engine.  Result status: ``search``.

Returns a dict with::

    status:        "doi" | "search" | "in_text" | "empty" | "not_citation"
    url:           the URL to open
    doi:           the resolved DOI, or None
    title:         the parsed title (best effort)
    engine_used:   id of the engine whose URL was returned
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from .detector import detect_in_text_citation, is_likely_citation
from .engines import Engine, by_stage
from .parser import parse_fields
from .platform_utils import (
    IS_DARWIN,
    build_scholar_url,
    clean_zotero_noise,
    is_zotero_installed,
    lookup_zotero_item_by_doi,
    resolve_publisher_url,
    zotero_select_url,
)
from .resolver import (
    build_doi_url,
    build_search_url,
    doi_url,
    resolve_doi,
)

_LOG = logging.getLogger(__name__)


def lookup(
    text: str,
    *,
    engines: Iterable[Engine],
    mailto: Optional[str] = None,
    route_mode: str = "auto",
) -> dict:
    """Resolve *text* to a URL using the supplied *engines*.

    See module docstring for the full pipeline.

    *route_mode* selects how a full reference is handled once a DOI
    has been resolved.  Three values, all case-insensitive:

    * ``"auto"`` (default) — open the DOI URL if we resolved one,
      otherwise open the search-engine URL.  Mirrors the v1.1 behaviour.
    * ``"search_always"`` — always open a search-engine URL, even when
      a DOI is in hand.  Useful when the user has Zotero's "Open in
      Zotero" / browser-connector installed, which intercepts
      ``doi.org`` URLs and re-opens the *currently-selected* Zotero
      item, so the browser always shows whatever PDF is in focus.
      Routing to ``scholar.google.com`` instead dodges that hook
      entirely — Scholar has its own DOI resolver and a search-results
      page is the same regardless of which paper is open in Zotero.
    * ``"doi_always"`` — open the DOI URL even when a search engine
      would have given a more useful result.  For users who never want
      a search engine page.

    ``route_mode`` does **not** affect in-text citations: those always
    go to a search engine (Stage 1 below) because the author + year
    alone rarely resolves to a specific DOI via Crossref.

    Returns a dict with::

        status:        "doi" | "search" | "in_text" | "empty" | "not_citation"
        url:           the URL to open
        doi:           the resolved DOI, or None
        title:         the parsed title (best effort)
        engine_used:   id of the engine whose URL was returned
    """
    if not text or not text.strip():
        return _empty_result()

    engine_list: List[Engine] = list(engines)
    mode = (route_mode or "auto").lower()

    # Stage 1: in-text citation short-circuit.
    #
    # When the user selects an in-text citation (e.g. "(Heidari, 2025)"
    # in the body of a paper), we don't have enough information to find
    # the DOI directly — Crossref's query.bibliographic on an 8-40
    # character author-year string returns mostly noise.  Instead we
    # route the parsed author + year straight to the search engines,
    # which have full-text indexes designed for exactly this kind of
    # query.  The user-visible behaviour is "instant search" rather
    # than "wait 1-2 s, then probably open the wrong paper".
    in_text = detect_in_text_citation(text)
    if in_text is not None:
        fields = {
            "title": None,
            "author": in_text.get("author"),
            "year": in_text.get("year"),
            "doi": None,
        }
        url = build_search_url(text, fields, engine_list, mailto=mailto)
        if url is None:
            # No search engine enabled — build a Scholar URL from the
            # parsed fields so the hotkey still does something useful.
            url = _scholar_fallback(in_text["author"], in_text["year"])
            engine_used = "scholar_fallback"
        else:
            engine_used = _first_enabled_id(engine_list, "search_url") or "search_url"
        return {
            "status": "in_text",
            "url": url,
            "doi": None,
            "title": None,
            "engine_used": engine_used,
            "in_text": in_text,
            "bypass_reason": None,
        }

    # Stage 2: full-reference gate.
    if not is_likely_citation(text):
        return _not_citation_result()

    # Stage 3: parse fields.
    fields = parse_fields(text)
    doi = fields.get("doi")

    # Stage 4: ask a doi_resolver engine for a DOI if we don't have one.
    if not doi:
        doi = resolve_doi(text, engine_list, mailto=mailto)

    # Stage 4.5: Zotero bypass.
    #
    # If the user has Zotero installed (with the macOS app, its
    # browser connector, or its "Open in Zotero" feature), opening
    # a doi.org URL will be intercepted and re-routed to the
    # CURRENTLY-OPENED PDF in Zotero — NOT the paper the user
    # actually selected.  This makes ``route_mode = "auto"`` useless
    # for Zotero users: every selection opens whatever PDF is in
    # Zotero's reader pane.
    #
    # The new fix tries to open the paper *inside Zotero itself* via
    # ``zotero://select/library/items/<KEY>`` — Zotero's own URL
    # scheme, registered with macOS — so Zotero brings the right
    # item to the front in its own reader.  This is more reliable
    # than publisher URLs, which some Zotero Safari-connector
    # configurations also intercept.  Falling back to publisher
    # direct URLs and Scholar search covers cases where the DOI
    # isn't in the user's library.
    bypass_reason: Optional[str] = None
    if (
        doi
        and mode == "auto"
        and IS_DARWIN
        and is_zotero_installed()
    ):
        bypass_reason = (
            "Zotero is installed \u2014 using Zotero's own URL scheme / "
            "publisher page instead of doi.org to avoid Zotero "
            "intercepting the URL and showing the currently-open PDF "
            "instead of the selected paper.  Switch the Routing mode "
            "to \u2018Always DOI\u2019 in the menu to force doi.org."
        )
        _LOG.info("Zotero auto-bypass active: %s", bypass_reason)

    # Stage 5: if we have a DOI, prefer a doi_url engine — unless the
    # user has explicitly chosen ``search_always``, or the Zotero
    # bypass is active (see Stage 4.5).
    if doi and mode != "search_always" and bypass_reason is None:
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
            "bypass_reason": bypass_reason,
        }

    # Stage 6: no DOI, or search_always, or Zotero bypass — build a
    # search URL.  When the Zotero bypass is active we try five things
    # in order of preference:
    #
    # 1. **Zotero select URL** — if the DOI exists in the user's local
    #    Zotero library, open ``zotero://select/library/items/<KEY>``.
    #    Zotero itself handles this URL (it's registered as a
    #    ``zotero://`` scheme handler), bringing the right item to
    #    the front regardless of what's currently open.  This is the
    #    most reliable path for users who keep their library in Zotero,
    #    because there's no browser involved to intercept anything.
    # 2. **Publisher URL** — follow the doi.org redirect server-side
    #    and open the publisher's direct URL (e.g.
    #    ``tandfonline.com/doi/full/...``).  In practice some Zotero
    #    installations also intercept publisher URLs via the Safari
    #    connector, but most users get the right page this way.
    # 3. **Scholar search by DOI** — Scholar handles DOI queries well.
    # 4. **Scholar search by cleaned text** — fall back to text-based
    #    search when no DOI is available.
    if bypass_reason:
        # 1. Try Zotero's own URL scheme (most reliable — no browser).
        zotero_url = None
        if doi:
            z_item = lookup_zotero_item_by_doi(doi)
            if z_item and z_item.get("key"):
                zotero_url = zotero_select_url(z_item["key"])
        if zotero_url:
            _LOG.info("Zotero bypass: opened Zotero item via %s", zotero_url)
            return {
                "status": "doi",
                "url": zotero_url,
                "doi": doi,
                "title": fields.get("title"),
                "engine_used": "zotero_select",
                "bypass_reason": bypass_reason,
            }

        # 2. Publisher direct URL.
        publisher_url = None
        if doi:
            publisher_url = resolve_publisher_url(doi)
        if publisher_url:
            _LOG.info("Zotero bypass: opened publisher URL %s", publisher_url)
            return {
                "status": "doi",
                "url": publisher_url,
                "doi": doi,
                "title": fields.get("title"),
                "engine_used": "publisher_direct",
                "bypass_reason": bypass_reason,
            }
        # 3 / 4. Scholar search fallback.
        if doi:
            url = build_scholar_url(doi)
        else:
            url = build_scholar_url(clean_zotero_noise(text))
        engine_used = "scholar_zotero_bypass"
    else:
        url = build_search_url(text, fields, engine_list, mailto=mailto)
        if url is None:
            # Last-ditch: use the canonical Google Scholar URL even if
            # the user has disabled every search engine.  We still
            # want *some* result so the user knows the hotkey fired.
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
        "doi": doi,
        "title": fields.get("title"),
        "engine_used": engine_used,
        "bypass_reason": bypass_reason,
    }


def _scholar_fallback(author: Optional[str], year: Optional[str]) -> str:
    """Build a Google Scholar search URL from parsed author + year.

    Used when the in-text short-circuit fires but the user has disabled
    every ``search_url`` engine.
    """
    import urllib.parse
    parts = [p for p in (author, year) if p]
    return "https://scholar.google.com/scholar?q=" + urllib.parse.quote(
        " ".join(parts)
    )


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
        "bypass_reason": None,
    }


def _not_citation_result() -> dict:
    return {
        "status": "not_citation",
        "url": "",
        "doi": None,
        "title": None,
        "engine_used": None,
        "bypass_reason": None,
    }


def _first_enabled_id(engines: List[Engine], stage: str) -> Optional[str]:
    for e in by_stage(engines, stage):
        return e.id
    return None


def _safe_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text or "")


__all__ = ["lookup"]
