"""Shared pytest fixtures and patches for the citation_hop test suite.

The most important job this file does is *neutralise the Zotero
auto-bypass* (added in v1.2.4) for the entire test run by default.

Why
---
``main.lookup`` checks ``is_zotero_installed()`` at call time.  On
the developer's Mac, Zotero really is installed (under
``/Applications/Zotero.app``), so the bypass fires and every test
that expects a doi.org URL gets a Scholar URL instead.  The previous
test suite pre-dated the bypass and asserted doi.org URLs, so it
broke the moment the user installed Zotero.

We patch ``is_zotero_installed`` to return ``False`` in **all**
tests by default — this preserves the existing test contract
("a known reference with a known DOI returns a doi.org URL") and
makes the bypass opt-in for the new tests that explicitly want to
exercise it.

Tests that need the bypass to be active should ``monkeypatch`` the
return value back to ``True`` (see
``tests/test_lookup.py::test_zotero_bypass_routes_to_scholar`` for
an example).

We patch BOTH:
* ``citation_hop.platform_utils.is_zotero_installed``  (the canonical home)
* ``citation_hop.main.is_zotero_installed``           (the imported alias)

because ``main.py`` does ``from .platform_utils import
is_zotero_installed`` at module load, so the ``main`` module's
binding to the function is fixed.  Patching only the canonical
home leaves ``main.is_zotero_installed`` pointing at the real
function — which still returns True on the dev's Mac.

We also patch ``lookup_zotero_item_by_doi`` (added in v1.3.0)
to return ``None`` so the new Zotero ``zotero://select`` deep-link
fallback never fires in tests unless explicitly opted in.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_zotero_bypass(monkeypatch):
    """Default every test to "Zotero not installed" and "DOI not in
    Zotero library".

    Individual tests that want the bypass active should re-patch
    inside the test body:

        def test_zotero_bypass(monkeypatch):
            monkeypatch.setattr(
                "citation_hop.main.is_zotero_installed", lambda: True
            )
            ...
    """
    monkeypatch.setattr(
        "citation_hop.platform_utils.is_zotero_installed", lambda: False
    )
    monkeypatch.setattr(
        "citation_hop.main.is_zotero_installed", lambda: False
    )
    # v1.3.0: also neutralise the new ``zotero://select`` deep-link
    # fallback.  On the dev's Mac the DOI *is* in the Zotero library
    # (because the developer has been adding papers while developing),
    # so the deep-link fires and overrides the expected Scholar URL.
    monkeypatch.setattr(
        "citation_hop.platform_utils.lookup_zotero_item_by_doi",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "citation_hop.main.lookup_zotero_item_by_doi",
        lambda doi: None,
    )
    yield
