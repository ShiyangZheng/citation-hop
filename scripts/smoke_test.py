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
    except AssertionError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print("\nALL GREEN — safe to launch the GUI now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
