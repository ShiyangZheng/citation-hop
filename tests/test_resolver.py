"""Tests for the Crossref resolver."""

import requests
import responses

from citation_hop import resolver


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
    # Crossref returns a totally unrelated title, so we should refuse.
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


def test_doi_url():
    assert resolver.doi_url("10.1038/nature12373") == (
        "https://doi.org/10.1038/nature12373"
    )


def test_build_scholar_url_uses_title_year_author():
    url = resolver.build_scholar_url(
        {
            "title": "Idiom acquisition in L2 learners",
            "author": "Zheng, S.",
            "year": "2024",
        }
    )
    assert "scholar.google.com/scholar" in url
    assert "Idiom" in url
    assert "Zheng" in url
    assert "2024" in url


def test_build_scholar_url_fallback_on_empty():
    url = resolver.build_scholar_url({})
    assert url.startswith("https://scholar.google.com/scholar?q=")
