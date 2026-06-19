"""Tests for the top-level lookup() function (no UI)."""

import responses

from citation_hop import resolver
from citation_hop.main import lookup


APA = (
    "Smith, J. (2019). An important paper on things. "
    "Journal of Important Things, 12(3), 45-67. "
    "https://doi.org/10.1038/nature12373"
)


def test_lookup_empty():
    r = lookup("")
    assert r["status"] == "empty"


def test_lookup_not_citation():
    r = lookup("The quick brown fox jumps over the lazy dog.")
    assert r["status"] == "not_citation"


def test_lookup_with_doi_in_text():
    r = lookup(APA)
    assert r["status"] == "doi"
    assert r["doi"] == "10.1038/nature12373"
    assert r["url"].endswith("10.1038/nature12373")


@responses.activate
def test_lookup_falls_back_to_crossref_then_scholar():
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
    r = lookup(text)
    assert r["status"] == "scholar"
    assert "scholar.google.com" in r["url"]


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
    r = lookup(text)
    assert r["status"] == "doi"
    assert r["doi"] == "10.1038/nature12373"
