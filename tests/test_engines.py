"""Tests for the engine registry (engines.py)."""

import pytest

from citation_hop.engines import (
    Engine,
    STAGE_DOI_RESOLVER,
    STAGE_DOI_URL,
    STAGE_SEARCH_URL,
    by_stage,
    default_engines,
    engines_from_dicts,
    engines_to_dicts,
    find_by_id,
    get_path,
    sort_by_order,
)


# ---------------------------------------------------------------------------
# Default engine list
# ---------------------------------------------------------------------------


def test_defaults_contain_crossref():
    engines = default_engines()
    cr = find_by_id(engines, "crossref")
    assert cr is not None
    assert cr.stage == STAGE_DOI_RESOLVER
    assert cr.enabled


def test_defaults_have_doi_url():
    engines = default_engines()
    doi_urls = by_stage(engines, STAGE_DOI_URL)
    assert any(e.id == "doi_org" and e.enabled for e in doi_urls)


def test_defaults_have_search_engines():
    engines = default_engines()
    # Use the full list (not by_stage) since by_stage filters by enabled.
    search = [e for e in engines if e.stage == STAGE_SEARCH_URL]
    # Google Scholar should be enabled by default (preserves v1.0 behaviour).
    assert any(e.id == "scholar" and e.enabled for e in search)
    # At least 5 other mainstream search engines should be present
    # (and disabled by default).
    others = [e for e in search if e.id != "scholar"]
    assert len(others) >= 5
    assert all(not e.enabled for e in others)


def test_defaults_cover_mainstream_platforms():
    """Spot-check the mainstream platforms we promised in the README."""
    engines = default_engines()
    expected = {
        "crossref", "doi_org", "scholar", "semantic_scholar",
        "openalex", "arxiv", "pubmed", "dblp", "base",
        "connected_papers", "litmaps", "researchgate", "core",
        "dimensions",
    }
    actual = {e.id for e in engines}
    missing = expected - actual
    assert not missing, f"missing mainstream engines: {missing}"


# ---------------------------------------------------------------------------
# Engine.render()
# ---------------------------------------------------------------------------


def test_render_doi_url():
    e = Engine(
        id="test", name="Test", stage=STAGE_DOI_URL,
        url_template="https://doi.org/{doi}",
    )
    out = e.render(doi="10.1234/abc", query="", fields={}, mailto="")
    assert out == "https://doi.org/10.1234/abc"


def test_render_search_url_uses_query():
    e = Engine(
        id="test", name="Test", stage=STAGE_SEARCH_URL,
        url_template="https://example.com/?q={query}",
    )
    out = e.render(doi=None, query="hello world", fields={}, mailto="")
    assert "hello%20world" in out or "hello+world" in out


def test_render_search_url_uses_fields():
    e = Engine(
        id="test", name="Test", stage=STAGE_SEARCH_URL,
        url_template="https://example.com/?t={title}&a={author}&y={year}",
    )
    out = e.render(
        doi=None, query="x",
        fields={"title": "Hello", "author": "Smith, J.", "year": "2020"},
        mailto="",
    )
    assert "Hello" in out
    assert "Smith" in out
    assert "2020" in out


def test_render_doi_url_without_doi_raises():
    e = Engine(
        id="test", name="Test", stage=STAGE_DOI_URL,
        url_template="https://doi.org/{doi}",
    )
    with pytest.raises(ValueError, match="requires a DOI"):
        e.render(doi=None, query="x", fields={}, mailto="")


def test_render_search_url_without_required_placeholder_works():
    """A search_url engine that doesn't reference {doi} should still
    work even when doi=None."""
    e = Engine(
        id="test", name="Test", stage=STAGE_SEARCH_URL,
        url_template="https://example.com/?q={query}",
    )
    out = e.render(doi=None, query="hi", fields={}, mailto="")
    assert "hi" in out


def test_render_unknown_placeholder_raises():
    e = Engine(
        id="test", name="Test", stage=STAGE_SEARCH_URL,
        url_template="https://example.com/?x={nonexistent}",
    )
    with pytest.raises(ValueError, match="unknown placeholder"):
        e.render(doi=None, query="x", fields={}, mailto="")


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


def test_engines_to_dicts_drops_empty_optionals():
    e = Engine(id="x", name="X", stage=STAGE_SEARCH_URL, url_template="https://x")
    d = engines_to_dicts([e])[0]
    assert d["id"] == "x"
    # Empty optionals should be removed for config.json readability.
    assert "api_url" not in d
    assert "api_params" not in d
    assert "api_doi_path" not in d


def test_engines_round_trip():
    original = default_engines()
    dicts = engines_to_dicts(original)
    parsed = engines_from_dicts(dicts)
    assert len(parsed) == len(original)
    for o, p in zip(sort_by_order(original), sort_by_order(parsed)):
        assert o.id == p.id
        assert o.name == p.name
        assert o.stage == p.stage
        assert o.enabled == p.enabled
        assert o.url_template == p.url_template


def test_engines_from_dicts_drops_invalid():
    dicts = [
        {"id": "ok", "name": "OK", "stage": "search_url",
         "url_template": "https://x"},
        {"id": "bad_stage", "name": "Bad", "stage": "nonsense"},
        {"name": "No ID", "stage": "search_url"},  # missing id
        "not a dict at all",
    ]
    parsed = engines_from_dicts(dicts)
    assert len(parsed) == 1
    assert parsed[0].id == "ok"


# ---------------------------------------------------------------------------
# by_stage / sort_by_order
# ---------------------------------------------------------------------------


def test_by_stage_filters_disabled():
    engines = [
        Engine(id="a", name="A", stage=STAGE_SEARCH_URL, enabled=True),
        Engine(id="b", name="B", stage=STAGE_SEARCH_URL, enabled=False),
        Engine(id="c", name="C", stage=STAGE_DOI_URL, enabled=True),
    ]
    assert [e.id for e in by_stage(engines, STAGE_SEARCH_URL)] == ["a"]


def test_sort_by_order_ascending():
    engines = [
        Engine(id="c", name="C", stage=STAGE_SEARCH_URL, order=3),
        Engine(id="a", name="A", stage=STAGE_SEARCH_URL, order=1),
        Engine(id="b", name="B", stage=STAGE_SEARCH_URL, order=2),
    ]
    assert [e.id for e in sort_by_order(engines)] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# get_path()
# ---------------------------------------------------------------------------


def test_get_path_dict_keys():
    data = {"a": {"b": {"c": 42}}}
    assert get_path(data, "a.b.c") == 42


def test_get_path_list_index():
    data = {"items": [{"DOI": "10.1/abc"}]}
    assert get_path(data, "items.0.DOI") == "10.1/abc"


def test_get_path_missing_returns_none():
    data = {"a": 1}
    assert get_path(data, "a.b.c") is None
    assert get_path(data, "x.y") is None
    assert get_path(data, "a.0") is None  # trying to index a non-list
