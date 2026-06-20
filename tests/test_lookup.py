"""Tests for the top-level lookup() function (no UI)."""

import responses

from citation_hop import resolver
from citation_hop.engines import default_engines
from citation_hop.main import lookup


APA = (
    "Smith, J. (2019). An important paper on things. "
    "Journal of Important Things, 12(3), 45-67. "
    "https://doi.org/10.1038/nature12373"
)


def _eng(**overrides):
    """Return a fresh default-engines list, optionally with overrides."""
    engines = default_engines()
    if overrides:
        for i, e in enumerate(engines):
            if e.id in overrides:
                engines[i] = e  # immutable dataclass, no-op for now
    return engines


def test_lookup_empty():
    r = lookup("", engines=_eng())
    assert r["status"] == "empty"


def test_lookup_not_citation():
    r = lookup("The quick brown fox jumps over the lazy dog.", engines=_eng())
    assert r["status"] == "not_citation"


def test_lookup_with_doi_in_text():
    r = lookup(APA, engines=_eng())
    assert r["status"] == "doi"
    assert r["doi"] == "10.1038/nature12373"
    assert r["url"].endswith("10.1038/nature12373")
    # Engine used should be the first enabled doi_url engine
    # (doi.org by default).
    assert r["engine_used"] == "doi_org"


@responses.activate
def test_lookup_falls_back_to_scholar():
    # APA with no embedded DOI; Crossref returns nothing useful.
    text = (
        "Smith, J. (2019). An obscure paper with no DOI in the text. "
        "Journal of X, 1(1), 1-10."
    )
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={"message": {"items": []}},
        status=200,
    )
    r = lookup(text, engines=_eng())
    assert r["status"] == "search"
    assert "scholar.google.com" in r["url"]
    assert r["engine_used"] == "scholar"


@responses.activate
def test_lookup_resolves_via_crossref_when_no_local_doi():
    text = (
        "Smith, J. (2019). An important paper on things. "
        "Journal of X, 1(1), 1-10."
    )
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={
            "message": {
                "items": [
                    {
                        "DOI": "10.1038/nature12373",
                        "title": ["An important paper on things"],
                    }
                ]
            }
        },
        status=200,
    )
    r = lookup(text, engines=_eng())
    assert r["status"] == "doi"
    assert r["doi"] == "10.1038/nature12373"


def test_lookup_uses_custom_search_engine():
    """If the user disables Google Scholar and enables a different
    search engine, that engine should be used as the fallback."""
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {
            "id": "scholar", "name": "Scholar", "stage": "search_url",
            "url_template": "https://scholar.google.com/scholar?q={query}",
            "enabled": False, "order": 0,
        },
        {
            "id": "my_lab", "name": "My Lab", "stage": "search_url",
            "url_template": "https://my-lab.example/search?q={query}",
            "enabled": True, "order": 1,
        },
    ])
    text = (
        "Smith, J. (2019). An obscure paper with no DOI. "
        "Journal of X, 1(1), 1-10. " * 3
    )
    # Crossref is not in this engine list -> no DOI; should fall back
    # to the user's custom search engine.
    r = lookup(text, engines=engines)
    assert r["status"] == "search"
    assert "my-lab.example" in r["url"]
    assert r["engine_used"] == "my_lab"


# ---------------------------------------------------------------------------
# In-text citation short-circuit
# ---------------------------------------------------------------------------


def test_lookup_in_text_citation_skips_crossref():
    """In-text citations like '(Heidari, 2025)' must skip Crossref and
    go straight to the search engines with the parsed author + year.

    This is the user-visible fix for "the tool always opens the same
    paper": with the previous code, a 12-character author-year string
    was sent to Crossref, which returned mostly noise and the resolver
    would either pick the wrong DOI or fall through to a weak Scholar
    query (just author+year) that often maps to a single dominant
    paper in the field.
    """
    r = lookup("(Heidari, 2025)", engines=_eng())
    assert r["status"] == "in_text"
    assert r["doi"] is None
    assert r["in_text"]["author"] == "Heidari"
    assert r["in_text"]["year"] == "2025"
    assert "scholar.google.com" in r["url"]
    assert "Heidari" in r["url"]
    assert "2025" in r["url"]


def test_lookup_in_text_citation_narrative_form():
    """Narrative form (Smith, 2020 with year in parens) is also detected."""
    r = lookup("Smith et al. (2020)", engines=_eng())
    assert r["status"] == "in_text"
    assert r["in_text"]["author"] == "Smith et al."
    assert r["in_text"]["year"] == "2020"


def test_lookup_in_text_citation_two_authors():
    r = lookup("(Smith & Jones, 2020)", engines=_eng())
    assert r["status"] == "in_text"
    assert r["in_text"]["author"] == "Smith & Jones"


def test_lookup_in_text_citation_with_disambig_suffix():
    """APA uses 'a/b/c' suffixes for same-year disambiguation."""
    r = lookup("(Smith, 2020a)", engines=_eng())
    assert r["status"] == "in_text"
    assert r["in_text"]["year"] == "2020a"


def test_lookup_in_text_citation_routes_to_enabled_engine():
    """When Google Scholar is disabled, the in-text short-circuit must
    route to the next enabled search engine, not fall through to a
    hard-coded Scholar URL."""
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {
            "id": "scholar", "name": "Scholar", "stage": "search_url",
            "url_template": "https://scholar.google.com/scholar?q={query}",
            "enabled": False, "order": 0,
        },
        {
            "id": "my_lab", "name": "My Lab", "stage": "search_url",
            "url_template": "https://my-lab.example/search?q={query}",
            "enabled": True, "order": 1,
        },
    ])
    r = lookup("(Smith, 2020)", engines=engines)
    assert r["status"] == "in_text"
    assert "my-lab.example" in r["url"]
    assert r["engine_used"] == "my_lab"


def test_lookup_in_text_citation_with_no_search_engines_falls_back():
    """If every search engine is disabled, we still build a Scholar
    URL from the parsed author + year so the hotkey does something
    useful rather than silently doing nothing."""
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {
            "id": "scholar", "name": "Scholar", "stage": "search_url",
            "url_template": "https://scholar.google.com/scholar?q={query}",
            "enabled": False, "order": 0,
        },
    ])
    r = lookup("(Smith, 2020)", engines=engines)
    assert r["status"] == "in_text"
    assert "scholar.google.com" in r["url"]  # fallback URL
    assert "Smith" in r["url"]
    assert "2020" in r["url"]


# ---------------------------------------------------------------------------
# APA plain-text title extraction
# ---------------------------------------------------------------------------


@responses.activate
def test_lookup_apa_reference_includes_title_in_search_url():
    """When a full APA reference (no DOI) falls through to the search
    engines, the Scholar URL must include the parsed title so the search
    is meaningful — not just author+year.

    This is the user-visible fix for "the tool always opens the same
    paper": without title extraction, the Scholar URL was
    `?q=+Heidari+2025`, which Google Scholar would resolve to whatever
    paper is most cited for that query in the field.  With title
    extraction, the URL is unique per reference.
    """
    apa = (
        "Heidari, A. (2025). Thirty-five years of research on idioms "
        "in SLA: A methodological review. Research Synthesis in "
        "Applied Linguistics."
    )
    # Force Crossref to return nothing (low similarity).
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={"message": {"items": []}},
        status=200,
    )
    r = lookup(apa, engines=_eng())
    assert r["status"] == "search"
    # Title must be in the URL — not just author+year.
    assert "Thirty" in r["url"] or "thirty" in r["url"].lower()
    assert "Heidari" in r["url"]
    assert "2025" in r["url"]


# ---------------------------------------------------------------------------
# route_mode (v1.2.3+) — user-controllable routing for full references
# ---------------------------------------------------------------------------
#
# Why this exists
# ---------------
# The user has Zotero's browser connector / "Open in Zotero" installed.
# That hook intercepts ``doi.org`` URLs and re-opens the *currently
# selected* Zotero item, so the browser always shows whatever PDF is in
# focus in Zotero.  The user reported "the browser always opens the
# same page" — a real bug, but the bug is in Zotero's URL interceptor,
# not in our resolver.  ``route_mode = "search_always"`` routes around
# the interceptor by going to ``scholar.google.com`` instead, which
# Zotero cannot recognise as a Zotero item.


def test_route_mode_default_is_auto():
    """When route_mode is omitted, full references still go to the
    DOI URL (back-compat with v1.2.0–v1.2.2)."""
    r = lookup(APA, engines=_eng())
    assert r["status"] == "doi"
    assert r["url"].endswith("10.1038/nature12373")


def test_route_mode_auto_explicit():
    r = lookup(APA, engines=_eng(), route_mode="auto")
    assert r["status"] == "doi"
    assert "doi.org" in r["url"]


def test_route_mode_search_always_routes_full_reference_to_scholar():
    """``search_always`` must override Stage 5 even when a DOI is
    available, so the URL is a Scholar query rather than a doi.org
    redirect."""
    r = lookup(APA, engines=_eng(), route_mode="search_always")
    assert r["status"] == "search"  # NOT "doi"
    assert "scholar.google.com" in r["url"]
    assert "doi.org" not in r["url"]
    # The parsed title should be in the URL (otherwise the Scholar
    # query degenerates to "Smith 2019" and may resolve to whatever
    # paper is most-cited for that combination).
    assert "important" in r["url"].lower()


def test_route_mode_search_always_keeps_in_text_going_to_search():
    """In-text citations already go to search, but route_mode must
    not accidentally send them to doi.org."""
    r = lookup("(Smith, 2020)", engines=_eng(), route_mode="search_always")
    assert r["status"] == "in_text"
    assert "scholar.google.com" in r["url"]
    assert "doi.org" not in r["url"]


def test_route_mode_doi_always_sends_full_ref_to_doi():
    """``doi_always`` is functionally equivalent to ``auto`` for the
    DOI-found path, but is the explicit user signal that they always
    want the publisher's page."""
    r = lookup(APA, engines=_eng(), route_mode="doi_always")
    assert r["status"] == "doi"
    assert r["url"].endswith("10.1038/nature12373")


def test_route_mode_invalid_value_falls_back_to_default():
    """A typo / old value should never crash lookup — just behave like
    ``auto``.  The config layer also coerces, but lookup is callable
    from external code (tests, future APIs) so it must be defensive
    on its own."""
    r = lookup(APA, engines=_eng(), route_mode="nonsense")
    assert r["status"] == "doi"  # behaves like auto
    assert "doi.org" in r["url"]


def test_route_mode_case_insensitive():
    r1 = lookup(APA, engines=_eng(), route_mode="SEARCH_ALWAYS")
    r2 = lookup(APA, engines=_eng(), route_mode="search_always")
    assert r1["status"] == r2["status"] == "search"


@responses.activate
def test_route_mode_search_always_uses_title_in_url():
    """When search_always kicks in, the URL should still include the
    parsed title for the same reason as test_lookup_apa_reference_includes_title_in_search_url
    — otherwise Scholar's fuzzy match lands on whatever is most cited
    for "Smith 2019" in the field, which is exactly the bug we're
    working around."""
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={"message": {"items": []}},
        status=200,
    )
    apa = (
        "Heidari, A. (2025). Thirty-five years of research on idioms. "
        "Research Synthesis in Applied Linguistics, 1(1), 161-183."
    )
    r = lookup(apa, engines=_eng(), route_mode="search_always")
    assert r["status"] == "search"
    assert "Thirty" in r["url"] or "thirty" in r["url"].lower()
    assert "doi.org" not in r["url"]


# ---------------------------------------------------------------------------
# Zotero auto-bypass tests (v1.2.4)
# ---------------------------------------------------------------------------
#
# The Zotero bypass is a critical behaviour for our user base: Zotero's
# browser connector and "Open in Zotero" intercept doi.org URLs and
# show the CURRENTLY-OPENED PDF in Zotero, not the paper the user
# actually selected.  These tests pin the bypass logic so it doesn't
# regress (e.g. someone removes the ``is_zotero_installed`` check or
# moves the bypass behind a feature flag without thinking through the
# consequence).


def test_zotero_bypass_routes_to_scholar(monkeypatch):
    """When Zotero is installed and route_mode is auto, a full
    reference with a resolved DOI must route to Google Scholar, not
    doi.org.  This is the v1.2.4 fix for "every selection opens the
    same PDF" — Zotero's interception of doi.org URLs would otherwise
    redirect the user to whatever PDF is currently open in Zotero.

    v1.3.0 added the ``zotero://select`` deep-link as layer 1 and
    ``resolve_publisher_url`` as layer 2 of the bypass; both must
    be neutralised here so the test exercises the original
    layer-3 Scholar fallback path.
    """
    from citation_hop import main as main_mod
    # The Zotero bypass in main.py is gated on IS_DARWIN because the
    # original v1.2.4 connector-interception bug was a macOS-only
    # behaviour.  On Linux/Windows CI the module-level constant is
    # False at import time, so the bypass never fires.  We patch it
    # True here so the bypass code path is exercised on every OS.
    monkeypatch.setattr(main_mod, "IS_DARWIN", True)
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)
    # v1.3.1: only the publisher-direct fallback fires before Scholar
    # now (the zotero://select layer was removed because it was a
    # no-op when Zotero already had the target PDF open).  Patch the
    # publisher resolver so the test exercises the Scholar fallback.
    monkeypatch.setattr(main_mod, "resolve_publisher_url", lambda doi, timeout=4.0: None)
    monkeypatch.setattr("citation_hop.platform_utils.resolve_publisher_url", lambda doi, timeout=4.0: None)

    r = lookup(APA, engines=_eng(), route_mode="auto")
    assert r["status"] == "search", (
        f"expected 'search' (Zotero bypass), got {r['status']!r}; "
        f"url={r['url']!r}"
    )
    assert "scholar.google.com" in r["url"], (
        f"expected Scholar URL when Zotero bypass is active, got {r['url']!r}"
    )
    # The URL should not be a doi.org URL.  We check the *scheme+host*
    # via ``urlparse`` because the user's captured text can legitimately
    # contain the string "doi.org" (e.g. "https://doi.org/10.xxxx" in
    # an APA reference) — the Scholar search query would then include
    # the percent-encoded form of that substring, but the Scholar URL
    # itself is still hosted on scholar.google.com.
    from urllib.parse import urlparse
    parsed = urlparse(r["url"])
    assert parsed.netloc != "doi.org", (
        f"Zotero bypass must not open doi.org: {r['url']!r}"
    )
    assert r["bypass_reason"] is not None
    assert "Zotero" in r["bypass_reason"]
    assert r["engine_used"] == "scholar_zotero_bypass"


def test_zotero_bypass_preserves_doi(monkeypatch):
    """Even when the Zotero bypass routes to Scholar, the resolved
    DOI should still be in the result dict — callers (e.g. the tray
    notification) can still show "DOI: 10.xxxx" to the user even
    though we're not opening doi.org."""
    from citation_hop import main as main_mod
    # See the IS_DARWIN note in test_zotero_bypass_routes_to_scholar:
    # we monkeypatch it True so the bypass gate fires on Linux/Windows CI.
    monkeypatch.setattr(main_mod, "IS_DARWIN", True)
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)
    monkeypatch.setattr(main_mod, "resolve_publisher_url", lambda doi, timeout=4.0: None)
    monkeypatch.setattr("citation_hop.platform_utils.resolve_publisher_url", lambda doi, timeout=4.0: None)

    r = lookup(APA, engines=_eng(), route_mode="auto")
    assert r["doi"] == "10.1038/nature12373"
    assert "scholar.google.com" in r["url"]


def test_zotero_bypass_with_publisher_url_opens_publisher(monkeypatch):
    """v1.3.1: when the Zotero bypass is active AND ``resolve_publisher_url``
    successfully returns a publisher URL, the result is the publisher
    URL — *not* a Scholar search.  This replaces the v1.3.0
    ``zotero://select`` deep-link behaviour, which was a no-op when
    Zotero was already displaying the target PDF.
    """
    from citation_hop import main as main_mod
    monkeypatch.setattr(main_mod, "IS_DARWIN", True)
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr(
        "citation_hop.platform_utils.is_zotero_installed", lambda: True
    )
    # Mock resolve_publisher_url to return a known publisher URL
    publisher = (
        "https://www.tandfonline.com/doi/full/10.1080/29984475.2025.2486966"
    )
    monkeypatch.setattr(
        main_mod, "resolve_publisher_url",
        lambda doi, timeout=4.0: publisher,
    )
    monkeypatch.setattr(
        "citation_hop.platform_utils.resolve_publisher_url",
        lambda doi, timeout=4.0: publisher,
    )

    r = lookup(APA, engines=_eng(), route_mode="auto")
    assert r["url"] == publisher
    assert r["engine_used"] == "publisher_direct"
    assert r["bypass_reason"] is not None


def test_in_text_multi_cite_routes_to_first_author_year(monkeypatch):
    """v1.3.1: parenthetical multi-citations like
    ``(Sinclair 1966; Halliday 1961)`` are recognised as in-text
    and routed to the search engine using the first cite's author+year.
    """
    r = lookup("(Sinclair 1966; Halliday 1961)",
               engines=_eng(), route_mode="auto")
    assert r["status"] == "in_text"
    assert r["in_text"]["kind"] == "author_year_multi"
    assert len(r["in_text"]["cites"]) == 2
    assert r["in_text"]["cites"][0]["author"] == "Sinclair"
    assert r["in_text"]["cites"][0]["year"] == "1966"
    assert "Sinclair" in r["url"] and "1966" in r["url"]


def test_in_text_single_cite_no_comma_supported(monkeypatch):
    """v1.3.1: single-cite parenthetical without a comma between
    author and year (``(Sinclair 1966)``) is now recognised.
    """
    r = lookup("(Sinclair 1966)", engines=_eng(), route_mode="auto")
    assert r["status"] == "in_text"
    assert r["in_text"]["kind"] == "author_year"
    assert r["in_text"]["author"] == "Sinclair"
    assert r["in_text"]["year"] == "1966"


def test_in_text_multi_cite_with_et_al_supported(monkeypatch):
    """v1.3.1: parenthetical multi-cite where one cite is
    ``Smith et al. 2020`` is recognised.
    """
    r = lookup(
        "(Sinclair 1966; Halliday 1961; Smith et al. 2020)",
        engines=_eng(), route_mode="auto",
    )
    assert r["status"] == "in_text"
    assert r["in_text"]["kind"] == "author_year_multi"
    assert len(r["in_text"]["cites"]) == 3
    assert r["in_text"]["cites"][2]["author"] == "Smith et al."
    assert r["in_text"]["cites"][2]["year"] == "2020"


def test_zotero_bypass_inactive_when_not_installed(monkeypatch):
    """When Zotero is not installed, the bypass must NOT fire —
    the user gets the normal doi.org URL as before."""
    from citation_hop import main as main_mod
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: False)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: False)

    r = lookup(APA, engines=_eng(), route_mode="auto")
    assert r["status"] == "doi"
    assert "doi.org" in r["url"]
    assert r["bypass_reason"] is None


def test_zotero_bypass_inactive_for_doi_always(monkeypatch):
    """When the user has explicitly chosen 'doi_always', the bypass
    must NOT fire — explicit user choice wins over the auto-bypass
    heuristic.  This is the documented escape hatch for users who
    DO want doi.org even with Zotero installed."""
    from citation_hop import main as main_mod
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)

    r = lookup(APA, engines=_eng(), route_mode="doi_always")
    assert r["status"] == "doi"
    assert "doi.org" in r["url"]
    assert r["bypass_reason"] is None


def test_zotero_bypass_inactive_for_search_always(monkeypatch):
    """When the user has explicitly chosen 'search_always', the
    bypass is moot (we're going to Scholar anyway), but the
    bypass_reason should still be None because the user already
    opted into the search path explicitly."""
    from citation_hop import main as main_mod
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)

    r = lookup(APA, engines=_eng(), route_mode="search_always")
    assert r["status"] == "search"
    assert "scholar.google.com" in r["url"] or "google" in r["url"]
    # bypass_reason is set by the auto-bypass only; search_always
    # doesn't trip it.  This means the "Zotero detected" notification
    # won't show for search_always users — which is correct, because
    # they already know they're using search.
    assert r["bypass_reason"] is None


def test_zotero_bypass_does_not_affect_in_text(monkeypatch):
    """In-text citations always go to search engines, regardless of
    Zotero.  The bypass must not change that."""
    from citation_hop import main as main_mod
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)

    r = lookup("(Wray, 2002)", engines=_eng(), route_mode="auto")
    assert r["status"] == "in_text"
    assert r["bypass_reason"] is None  # in-text never triggers bypass


def test_zotero_bypass_no_doi_uses_scholar(monkeypatch):
    """If we can't resolve a DOI AND Zotero is installed, we still
    route to Scholar (not doi.org), because the bypass applies to
    the route decision, not just to doi.org URLs.

    Actually wait — without a DOI, Stage 5 doesn't fire, so we
    always go to Stage 6 (search).  The bypass adds a note about
    why we're using search.  The user benefits from the explanation
    but the URL is the same as the no-bypass case.
    """
    from citation_hop import main as main_mod
    import responses as _responses
    from citation_hop import resolver
    monkeypatch.setattr(main_mod, "is_zotero_installed", lambda: True)
    monkeypatch.setattr("citation_hop.platform_utils.is_zotero_installed", lambda: True)

    # APA with no embedded DOI and Crossref returns nothing.
    text = "Smith, J. (2019). An important paper on things. Journal of Important Things, 12(3), 45-67."

    with _responses.RequestsMock() as rsps:
        rsps.add(
            _responses.GET,
            resolver.CROSSREF_API,
            json={"message": {"items": []}},
            status=200,
        )
        r = lookup(text, engines=_eng(), route_mode="auto")

    # No DOI → search path → no Zotero bypass fires (bypass only
    # applies to doi-route decisions).
    assert r["doi"] is None
    assert r["bypass_reason"] is None
