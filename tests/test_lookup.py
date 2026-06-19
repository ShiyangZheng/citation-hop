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
