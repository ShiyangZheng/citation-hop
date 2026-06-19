"""Tests for the Crossref resolver and the engine-driven URL builders."""

import requests
import responses

from citation_hop import resolver
from citation_hop.engines import default_engines


# ---------------------------------------------------------------------------
# Crossref resolver (legacy shim + the new resolve_doi())
# ---------------------------------------------------------------------------


@responses.activate
def test_resolve_via_crossref_hit():
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
    text = "Smith, J. (2019). An important paper on things."
    assert resolver.resolve_via_crossref(text) == "10.1038/nature12373"


@responses.activate
def test_resolve_via_crossref_low_score_rejected():
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={
            "message": {
                "items": [
                    {
                        "DOI": "10.1038/nature99999",
                        "title": ["Quantum chromodynamics in curved spacetime"],
                    }
                ]
            }
        },
        status=200,
    )
    text = "Smith, J. (2019). An important paper on things."
    assert resolver.resolve_via_crossref(text) is None


@responses.activate
def test_resolve_via_crossref_network_error_returns_none():
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        body=requests.ConnectionError("boom"),
    )
    assert resolver.resolve_via_crossref("anything") is None


@responses.activate
def test_resolve_via_crossref_empty_items():
    responses.add(
        responses.GET,
        resolver.CROSSREF_API,
        json={"message": {"items": []}},
        status=200,
    )
    assert resolver.resolve_via_crossref("anything") is None


# ---------------------------------------------------------------------------
# doi_url (the canonical helper)
# ---------------------------------------------------------------------------


def test_doi_url():
    assert resolver.doi_url("10.1038/nature12373") == (
        "https://doi.org/10.1038/nature12373"
    )


# ---------------------------------------------------------------------------
# Engine-driven URL builders (v1.1)
# ---------------------------------------------------------------------------


def test_build_doi_url_uses_first_enabled_doi_url_engine():
    engines = default_engines()
    url = resolver.build_doi_url(
        "10.1038/nature12373", engines, mailto="t@t.com"
    )
    assert url == "https://doi.org/10.1038/nature12373"


def test_build_doi_url_with_no_doi_url_engines_returns_none():
    """If all doi_url engines are disabled, return None (caller falls
    back to doi_url(doi))."""
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {"id": "doi_org", "name": "doi.org", "stage": "doi_url",
         "url_template": "https://doi.org/{doi}", "enabled": False},
    ])
    assert resolver.build_doi_url("10.1/abc", engines) is None


def test_build_search_url_uses_google_scholar_by_default():
    engines = default_engines()
    url = resolver.build_search_url(
        "Idiom acquisition in L2 learners",
        {"title": "Idiom acquisition in L2 learners",
         "author": "Zheng, S.",
         "year": "2024"},
        engines,
    )
    assert url is not None
    assert "scholar.google.com/scholar" in url
    # The default Google Scholar template uses title + author + year,
    # URL-encoded.  All three should be present in the final URL.
    assert "Idiom" in url
    assert "Zheng" in url
    assert "2024" in url


def test_build_search_url_uses_first_enabled():
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {"id": "scholar", "name": "Scholar", "stage": "search_url",
         "url_template": "https://scholar.google.com/scholar?q={query}",
         "enabled": False, "order": 0},
        {"id": "my_lab", "name": "My Lab", "stage": "search_url",
         "url_template": "https://my-lab.example/search?q={query}",
         "enabled": True, "order": 1},
    ])
    url = resolver.build_search_url("hello", {}, engines)
    assert url == "https://my-lab.example/search?q=hello"


def test_build_search_url_with_all_disabled_returns_none():
    from citation_hop.engines import engines_from_dicts
    engines = engines_from_dicts([
        {"id": "scholar", "name": "Scholar", "stage": "search_url",
         "url_template": "https://scholar.google.com/scholar?q={query}",
         "enabled": False, "order": 0},
    ])
    assert resolver.build_search_url("hello", {}, engines) is None
