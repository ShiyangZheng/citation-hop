#!/usr/bin/env python
"""Headless smoke test for citationHop — runs without a GUI.

Use this to verify the cross-platform refactor works on a fresh
checkout (CI, Windows, Linux) before you start the actual app.

Run:
    python scripts/smoke_test.py

Exit code 0 = all green. Non-zero = something is broken; the failing
assertion's diagnostic will be printed.
"""
from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def check_imports() -> None:
    banner("Importing all modules")
    modules = [
        "citation_hop",
        "citation_hop.engines",
        "citation_hop.parser",
        "citation_hop.detector",
        "citation_hop.extractor",
        "citation_hop.resolver",
        "citation_hop.clipboard",
        "citation_hop.config",
        "citation_hop.platform_utils",
        # tray + main NOT imported here — they need a display
    ]
    for name in modules:
        importlib.import_module(name)
        print(f"  ok: {name}")


def check_engines_default() -> None:
    banner("Default engine set")
    from citation_hop.engines import default_engines

    engines = default_engines()
    assert len(engines) >= 10, f"expected >= 10 default engines, got {len(engines)}"
    enabled = [e for e in engines if e.enabled]
    assert len(enabled) >= 2, "expected at least 2 default-enabled engines"
    print(f"  loaded {len(engines)} engines, {len(enabled)} enabled by default")
    for e in enabled:
        print(f"  - [{e.stage:13s}] {e.id:20s} {e.name}")


def check_engine_url_render() -> None:
    banner("Engine URL rendering")
    from citation_hop.engines import default_engines, by_stage

    engines = default_engines()
    doi = "10.1037/0003-066X.59.1.29"

    # DOI URL stage
    doi_url_engines = by_stage(engines, "doi_url")
    assert doi_url_engines, "no doi_url engines"
    url = doi_url_engines[0].render(doi=doi, query=doi, fields={}, mailto=None)
    assert doi in url or "doi.org" in url, f"DOI not in URL: {url}"
    print(f"  doi_url:  {url}")

    # Search URL stage
    search_engines = by_stage(engines, "search_url")
    enabled_search = [e for e in search_engines if e.enabled]
    assert enabled_search, "no enabled search_url engines"
    url = enabled_search[0].render(
        doi="", query=doi, fields={"title": "Mindfulness"}, mailto=None
    )
    assert "scholar" in url.lower() or "crossref" in url.lower() or "doi" in url.lower()
    print(f"  search:   {url}")


def check_config_io() -> None:
    banner("Config round-trip")
    from citation_hop.config import load_config, save_config, config_path

    # Use a temp path by writing/loading manually
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as td:
        from citation_hop import config as cfg_mod
        from citation_hop.engines import engines_from_dicts, engines_to_dicts, default_engines

        original_path = cfg_mod.config_path
        try:
            # Monkey-patch config_path to point at temp dir
            cfg_mod._config_dir = lambda: Path(td)  # type: ignore[attr-defined]
            cfg_mod.config_path = lambda: Path(td) / "config.json"  # type: ignore[assignment]

            # 1. Bare modifier form (legacy v1.0 storage) must be
            #    normalised to pynput 1.8+ angle-bracket form on load.
            cfg = load_config()
            cfg["hotkey"] = "ctrl+shift+d"
            save_config(cfg)

            cfg2 = load_config()
            assert cfg2["hotkey"] == "<ctrl>+<shift>+d", (
                f"bare hotkey not normalised: {cfg2['hotkey']!r}"
            )

            # 2. Already-normalised form must round-trip identity.
            cfg2["hotkey"] = "<ctrl>+<shift>+d"
            save_config(cfg2)

            cfg3 = load_config()
            assert cfg3["hotkey"] == "<ctrl>+<shift>+d", (
                f"hotkey not preserved: {cfg3['hotkey']!r}"
            )

            engines = engines_from_dicts(cfg3["engines"])
            assert len(engines) >= 3
            print(f"  round-tripped config OK, {len(engines)} engines")
        finally:
            cfg_mod.config_path = original_path  # type: ignore[assignment]


def check_resolver_engines() -> None:
    banner("Resolver uses engine list")
    from citation_hop.engines import default_engines
    from citation_hop.resolver import build_doi_url, build_search_url

    engines = default_engines()
    doi = "10.1037/0003-066X.59.1.29"
    url = build_doi_url(doi, engines=engines, mailto="test@example.com")
    assert url is not None, "build_doi_url returned None"
    assert doi in url or "doi.org" in url
    print(f"  build_doi_url:   {url}")

    url = build_search_url(doi, {}, engines=engines, mailto="test@example.com")
    assert url is not None, "build_search_url returned None"
    print(f"  build_search_url: {url}")


def check_platform_utils_imports() -> None:
    banner("platform_utils API surface")
    from citation_hop import platform_utils

    for name in ("confirm", "notify", "keystroke_label"):
        assert hasattr(platform_utils, name), f"missing {name}"
        print(f"  ok: platform_utils.{name}")


def check_in_text_citation_detection() -> None:
    banner("In-text citation detection + lookup")
    from citation_hop.detector import detect_in_text_citation
    from citation_hop.main import lookup
    from citation_hop.engines import default_engines

    cases = [
        ("(Smith, 2020)",                "Smith",        "2020"),
        ("Smith et al. (2020)",          "Smith et al.", "2020"),
        ("(Titone & Connine, 1999)",     "Titone & Connine", "1999"),
        ("(Heidari, 2025)",              "Heidari",      "2025"),
    ]
    for text, want_author, want_year in cases:
        got = detect_in_text_citation(text)
        assert got is not None, f"no match for {text!r}"
        assert got["author"] == want_author, f"author mismatch: {got['author']!r}"
        assert got["year"] == want_year, f"year mismatch: {got['year']!r}"

    # End-to-end: lookup() must short-circuit in-text citations to the
    # search engines, without calling Crossref.
    engines = default_engines()
    r = lookup("(Heidari, 2025)", engines=engines)
    assert r["status"] == "in_text", f"expected in_text, got {r['status']}"
    assert r["doi"] is None
    assert "scholar.google.com" in r["url"]
    assert "Heidari" in r["url"] and "2025" in r["url"]
    print(f"  in-text lookup URL: {r['url']}")

    # Negative: a paragraph that happens to contain "(Smith, 2020)"
    # must NOT match the in-text detector (gate requires whole-string match).
    prose = "Recent work (Smith, 2020) found that ..."
    assert detect_in_text_citation(prose) is None
    print("  in-text detector correctly rejects prose with embedded citation")


def check_apa_title_extraction() -> None:
    """Verify that the APA reference parser correctly extracts the
    title and routes to the right destination.

    Two acceptable outcomes exist for this test:

    1. **Crossref resolved it** — when the v1.3 multi-signal scoring
       gives the Heidari paper enough confidence to cross the
       ``SIMILARITY_THRESHOLD`` (0.60), ``lookup()`` returns a
       ``status='doi'`` with the publisher / doi.org URL.  This is
       the *happy path* — the user gets the exact paper.

    2. **Crossref did not resolve it** — if the live Crossref API
       response differs (e.g. the paper was retracted, or the
       scoring misses by 0.001), we fall through to Scholar.  The
       Scholar fallback URL must include the parsed title and author
       so the search is meaningful.

    The test accepts either outcome, but it must NOT crash.  This
    avoids CI flakes when the live Crossref corpus shifts.
    """
    banner("APA plain-text title extraction")
    from citation_hop.parser import parse_fields
    from citation_hop.main import lookup
    from citation_hop.engines import default_engines

    apa = (
        "Heidari, A. (2025). Thirty-five years of research on idioms in "
        "SLA: A methodological review. Research Synthesis in Applied "
        "Linguistics."
    )
    fields = parse_fields(apa)
    assert fields["title"] is not None, "APA title should be extracted"
    assert "Thirty-five years" in fields["title"]
    assert fields["year"] == "2025"
    print(f"  parsed title: {fields['title']!r}")
    print(f"  parsed author: {fields['author']!r}")
    print(f"  parsed year:   {fields['year']!r}")

    engines = default_engines()
    r = lookup(apa, engines=engines)
    print(f"  lookup status: {r['status']}")
    print(f"  resolved DOI:  {r.get('doi')!r}")
    print(f"  lookup URL:    {r['url'][:120]}...")
    sys.stdout.flush()

    # Either Crossref resolved it (v1.3 multi-signal scoring hit the
    # threshold), or it fell through to Scholar.  Both are valid.
    if r["status"] == "doi":
        # Crossref resolved.  The URL may be any of:
        # - doi.org URL           (no Zotero / bypass inactive)
        # - publisher direct URL  (Zotero bypass layer 2)
        # - zotero://select URL   (Zotero bypass layer 1, v1.3)
        # In the zotero:// case the DOI is NOT in the URL — it's a
        # Zotero item key instead.  So we only require that the DOI
        # is resolved (i.e. r["doi"] is a non-empty string) and the
        # URL is non-empty.  We don't require DOI ⊂ URL.
        assert r["doi"], (
            f"status='doi' must carry a resolved DOI, got {r.get('doi')!r}"
        )
        assert r["url"], (
            f"status='doi' must carry a non-empty URL, got {r['url']!r}"
        )
        print(f"  [OK] Crossref resolved: {r['doi']} -> {r['url'][:60]}...")
    elif r["status"] == "search":
        # Crossref did not resolve.  Scholar fallback must include
        # title + author so the search is meaningful.
        assert "Thirty" in r["url"], f"title not in Scholar URL: {r['url']}"
        assert "Heidari" in r["url"], f"author not in Scholar URL: {r['url']}"
        print(f"  [OK] Scholar fallback includes title+author")
    else:
        # Anything else is unexpected — fail loudly with the actual result.
        raise AssertionError(
            f"unexpected lookup status {r['status']!r}: {r!r}"
        )


def main() -> int:
    print(f"Python:  {sys.version.split()[0]}")
    print(f"System:  {platform.system()} {platform.release()}")
    print(f"CWD:     {Path.cwd()}")

    try:
        check_imports()
        check_engines_default()
        check_engine_url_render()
        check_config_io()
        check_resolver_engines()
        check_platform_utils_imports()
        check_in_text_citation_detection()
        check_apa_title_extraction()
    except AssertionError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print("\nALL GREEN -- safe to launch the GUI now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
