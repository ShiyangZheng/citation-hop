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
    resolve_publisher_url,
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
        # Multi-cite parenthetical form (Smith, 2020; Jones, 2021)
        # references multiple papers.  We can't open them all from one
        # hotkey press, so we route the *first* cite through the normal
        # search engine path — that's the paper the user is most
        # likely to want.  The result dict keeps the full cite list
        # under ``in_text.cites`` so the tray notification can say
        # "Smith, 2020 (1 of 2)" etc.
        if in_text.get("kind") == "author_year_multi" and in_text.get("cites"):
            first = in_text["cites"][0]
            author = first["author"]
            year = first["year"]
        else:
            author = in_text.get("author")
            year = in_text.get("year")
        fields = {
            "title": None,
            "author": author,
            "year": year,
            "doi": None,
        }
        url = build_search_url(text, fields, engine_list, mailto=mailto)
        if url is None:
            # No search engine enabled — build a Scholar URL from the
            # parsed fields so the hotkey still does something useful.
            url = _scholar_fallback(author, year)
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
    # search URL.  When the Zotero bypass is active we try two things
    # in order of preference:
    #
    # 1. **Publisher URL** — resolve the DOI via Crossref's
    #    ``resource.primary.URL`` / ``resource.secondary[].URL``
    #    field (one REST call to api.crossref.org), with a manual
    #    doi.org redirect-chain walk as fallback.  We hand the
    #    publisher URL (e.g. ``tandfonline.com/doi/full/...``,
    #    ``benjamins.com/catalog/z.lt2.68sin``) to ``webbrowser.open``.
    #    Zotero's connector only intercepts doi.org, so the publisher
    #    page opens correctly regardless of which PDF is currently
    #    showing in Zotero.
    #
    #    Earlier v1.3.0 had a "Zotero deep-link" layer that opened
    #    ``zotero://select/library/items/<KEY>``.  That layer was
    #    removed in v1.3.1 because ``zotero://select`` is a no-op
    #    when Zotero is already showing that very PDF — which is
    #    exactly the situation the user is in when they hit the
    #    hotkey to navigate away from it.
    #
    # 2. **Scholar search by DOI** — Scholar handles DOI queries
    #    well and returns the exact paper as the first result.
    #    Falls back to Scholar search by cleaned citation text when
    #    no DOI is available.
    if bypass_reason:
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
        # Fall back to Scholar — use the DOI string (cleanest query)
        # if we have it, otherwise the cleaned citation text.
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
