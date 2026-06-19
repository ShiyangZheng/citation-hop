"""Tests for ``citation_hop.config`` — defaults, migration, persistence.

These exist to lock in the cross-platform hotkey contract after we
shipped the v1.1.0 fix where pynput 1.8+ changed the modifier-key
syntax to require angle brackets.  The two key invariants are:

* ``DEFAULT_HOTKEY`` is always parseable by ``pynput.keyboard.HotKey.parse``
  on the current platform.
* Saved configs from older versions (bare ``cmd+shift+l``) are migrated
  to the new angle-bracketed form on load, with the ``cmd`` token
  rewritten to ``<ctrl>`` on non-Darwin platforms.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the src/ layout importable when running this file in isolation
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from citation_hop import config as cfg
from citation_hop.platform_utils import IS_DARWIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, hotkey: str) -> Path:
    """Write a minimal valid config file and point the module at it."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "hotkey": hotkey,
                "mailto": "test@example.com",
                "similarity_threshold": 0.85,
                "engines": [
                    # keep the engine list valid so we exercise hotkey migration
                    # without dragging engines migration logic in
                    {
                        "id": "doi_org",
                        "name": "doi.org",
                        "stage": "doi_url",
                        "url_template": "https://doi.org/{doi}",
                        "enabled": True,
                        "order": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return cfg_file


def _parse_with_pynput(s: str):
    """Wrap pynput's parser so the import is local to the test."""
    from pynput.keyboard import HotKey
    return HotKey.parse(s)


# ---------------------------------------------------------------------------
# 1. Default is platform-correct AND parseable
# ---------------------------------------------------------------------------


def test_default_hotkey_uses_angle_brackets():
    """The shipped default must use the pynput 1.8+ angle-bracket form."""
    if IS_DARWIN:
        assert cfg.DEFAULT_HOTKEY == "<cmd>+<shift>+l"
    else:
        assert cfg.DEFAULT_HOTKEY == "<ctrl>+<shift>+l"


def test_default_hotkey_is_pynput_parseable():
    """``pynput.keyboard.HotKey.parse`` must accept the default."""
    parsed = _parse_with_pynput(cfg.DEFAULT_HOTKEY)
    assert isinstance(parsed, list) and len(parsed) >= 2


def test_default_hotkey_has_no_bare_modifier():
    """Guard against the v1.0 regression (bare ``cmd`` token)."""
    for tok in ("cmd+", "ctrl+", "alt+", "shift+"):
        assert tok not in cfg.DEFAULT_HOTKEY, (
            f"DEFAULT_HOTKEY contains bare token {tok!r}: {cfg.DEFAULT_HOTKEY!r}"
        )


# ---------------------------------------------------------------------------
# 2. Migration of legacy configs
# ---------------------------------------------------------------------------


def test_legacy_bare_cmd_migrated_to_brackets(tmp_path, monkeypatch):
    """Old ``cmd+shift+l`` form must round-trip to pynput-parseable form."""
    _write_config(tmp_path, "cmd+shift+l")
    monkeypatch.setattr(cfg, "config_path", lambda: _write_config(tmp_path, "cmd+shift+l"))
    monkeypatch.setattr(cfg, "save_config", lambda c: None)  # don't write back

    loaded = cfg.load_config()
    expected = "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"
    assert loaded["hotkey"] == expected


def test_legacy_bracketed_cmd_preserved_on_macos(tmp_path, monkeypatch):
    """Already-bracketed <cmd>+<shift>+l stays unchanged on macOS."""
    if not IS_DARWIN:
        pytest.skip("macOS-only invariant")
    monkeypatch.setattr(cfg, "config_path", lambda: _write_config(tmp_path, "<cmd>+<shift>+l"))
    monkeypatch.setattr(cfg, "save_config", lambda c: None)
    assert cfg.load_config()["hotkey"] == "<cmd>+<shift>+l"


def test_legacy_bracketed_cmd_translated_to_ctrl_on_win(tmp_path, monkeypatch):
    """On Win/Linux, <cmd>+<shift>+l must become <ctrl>+<shift>+l."""
    if IS_DARWIN:
        pytest.skip("non-Darwin invariant")
    monkeypatch.setattr(cfg, "config_path", lambda: _write_config(tmp_path, "<cmd>+<shift>+l"))
    monkeypatch.setattr(cfg, "save_config", lambda c: None)
    assert cfg.load_config()["hotkey"] == "<ctrl>+<shift>+l"


def test_migrated_hotkey_is_pynput_parseable(tmp_path, monkeypatch):
    """End-to-end: a legacy config loads to a value pynput accepts."""
    _write_config(tmp_path, "cmd+shift+l")
    monkeypatch.setattr(cfg, "config_path", lambda: _write_config(tmp_path, "cmd+shift+l"))
    monkeypatch.setattr(cfg, "save_config", lambda c: None)

    loaded = cfg.load_config()
    # This will raise ValueError if the migration is incomplete
    parsed = _parse_with_pynput(loaded["hotkey"])
    assert parsed, "migrated hotkey must parse to a non-empty key list"


def test_migrated_hotkey_persists_to_disk(tmp_path, monkeypatch):
    """After load, the on-disk file holds the migrated form so the
    rewrite is a one-time cost, not paid on every launch."""
    target = _write_config(tmp_path, "cmd+shift+l")
    monkeypatch.setattr(cfg, "config_path", lambda: target)
    # Do NOT stub save_config — we want the real write to land on disk.

    cfg.load_config()
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    expected = "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"
    assert on_disk["hotkey"] == expected


def test_legacy_ctrl_unchanged(tmp_path, monkeypatch):
    """A legacy <ctrl>+<shift>+l config is left alone on all platforms."""
    monkeypatch.setattr(cfg, "config_path", lambda: _write_config(tmp_path, "<ctrl>+<shift>+l"))
    monkeypatch.setattr(cfg, "save_config", lambda c: None)
    assert cfg.load_config()["hotkey"] == "<ctrl>+<shift>+l"


# ---------------------------------------------------------------------------
# 3. ``set_hotkey`` round-trip
# ---------------------------------------------------------------------------


def test_set_hotkey_persists_brackets(tmp_path, monkeypatch):
    """``set_hotkey`` accepts the bracketed form and round-trips it."""
    target = _write_config(tmp_path, "<ctrl>+<shift>+k")
    monkeypatch.setattr(cfg, "config_path", lambda: target)
    monkeypatch.setattr(cfg, "_config_dir", lambda: tmp_path)

    cfg.set_hotkey("<ctrl>+<shift>+k")
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["hotkey"] == "<ctrl>+<shift>+k"
    # And it parses
    assert _parse_with_pynput(on_disk["hotkey"])


# ---------------------------------------------------------------------------
# 4. ``_normalise_hotkey`` unit tests — the migration primitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, want",
    [
        # Bare-token form (the v1.0 / v1.1 era)
        ("cmd+shift+l", "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"),
        ("ctrl+alt+d",  "<ctrl>+<alt>+d"),
        ("alt+shift+x", "<alt>+<shift>+x"),
        # Bracketed form (already migrated)
        ("<cmd>+<shift>+l", "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"),
        ("<ctrl>+<shift>+l", "<ctrl>+<shift>+l"),
        # Mixed: only the bare half gets wrapped
        ("<cmd>+shift+l", "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"),
        ("cmd+<shift>+l", "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"),
        # Trailing literal keys (digits / letters) stay bare
        ("ctrl+5", "<ctrl>+5"),
        ("alt+a", "<alt>+a"),
        # Sides matter: only bare ``cmd`` becomes ``<ctrl>`` on non-Darwin
        # (the bracketed version is handled in the same pass).
    ],
)
def test_normalise_hotkey_round_trip(raw, want):
    assert cfg._normalise_hotkey(raw) == want


def test_normalise_hotkey_idempotent():
    """Normalising a normalised value returns it unchanged."""
    once = cfg._normalise_hotkey("cmd+shift+l")
    twice = cfg._normalise_hotkey(once)
    assert once == twice


def test_normalise_then_parse_always_works():
    """Every legacy form must produce a string pynput accepts."""
    for raw in [
        "cmd+shift+l",
        "ctrl+shift+l",
        "alt+shift+x",
        "<cmd>+<shift>+l",
        "<ctrl>+<shift>+l",
        "<cmd>+shift+l",  # mixed legacy
    ]:
        normalised = cfg._normalise_hotkey(raw)
        parsed = _parse_with_pynput(normalised)
        assert parsed, f"pynput rejected {normalised!r} (from {raw!r})"
