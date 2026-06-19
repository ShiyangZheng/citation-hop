"""User-facing configuration for citationHop.

The config is a single JSON file in the OS-appropriate per-user
config directory:

* macOS:    ~/Library/Application Support/citationHop/config.json
* Windows:  %APPDATA%\\citationHop\\config.json
* Linux:    ~/.config/citationHop/config.json

The first-run defaults include the full search-engine list, with
Crossref + doi.org + Google Scholar pre-enabled (preserves the v1.0
"out-of-the-box" behaviour).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .engines import (
    default_engines,
    engines_from_dicts,
    engines_to_dicts,
)
from .platform_utils import IS_DARWIN, IS_WIN

APP_NAME = "citationHop"
# pynput GlobalHotKeys syntax.  Use the OS-native modifier: macOS = cmd,
# Windows / Linux = ctrl.  The bare combo is the same on all platforms.
DEFAULT_HOTKEY = "cmd+shift+l" if IS_DARWIN else "ctrl+shift+l"
DEFAULT_MAILTO = "syz@shiyangzheng.top"
DEFAULT_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Per-user config directory
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """Return (and create) the per-user config directory.

    Tries ``platformdirs`` first (the right answer on every OS); falls
    back to a hand-rolled mapping if ``platformdirs`` isn't installed
    (e.g. in a slimmed-down test env).
    """
    try:
        from platformdirs import user_config_dir  # type: ignore

        base = Path(user_config_dir(APP_NAME, appauthor=False, roaming=False))
    except ImportError:
        home = Path.home()
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", str(home))) / APP_NAME
        elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
            base = home / "Library" / "Application Support" / APP_NAME
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return _config_dir() / "config.json"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _defaults() -> Dict[str, Any]:
    return {
        "hotkey": DEFAULT_HOTKEY,
        "mailto": DEFAULT_MAILTO,
        "similarity_threshold": DEFAULT_THRESHOLD,
        "engines": engines_to_dicts(default_engines()),
    }


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Load the config, falling back to defaults on missing / bad file.

    We never crash the app over a config error.  On parse failure, the
    bad file is moved aside as ``config.json.bak`` and defaults are
    used.
    """
    path = config_path()
    if not path.exists():
        cfg = _defaults()
        save_config(cfg)
        return cfg

    try:
        with path.open("r", encoding="utf-8") as f:
            data: Any = json.load(f)
    except (OSError, json.JSONDecodeError):
        try:
            shutil.copy2(path, path.with_suffix(".json.bak"))
        except OSError:
            pass
        cfg = _defaults()
        save_config(cfg)
        return cfg

    if not isinstance(data, dict):
        cfg = _defaults()
        save_config(cfg)
        return cfg

    # Merge defaults so newly-added keys (and the engines list) still
    # work after an upgrade.  ``engines`` is a special case: we keep the
    # user's list (preserves customisations) but backfill any missing
    # built-in engine so newly-shipped engines show up automatically.
    merged = _defaults()
    for k, v in data.items():
        if k == "engines":
            continue  # handled below
        if k in merged:
            merged[k] = v

    # Normalise a stale hotkey for the current platform: a macOS-style
    # "cmd+..." combo crashes pynput on Windows / Linux.  Only swap when
    # "cmd" is not actually a valid key on this platform — on macOS we
    # leave it alone.
    if not IS_DARWIN and isinstance(merged.get("hotkey"), str):
        hk = merged["hotkey"].strip().lower()
        # "cmd" may appear as a bare token, e.g. "cmd+shift+l" or with
        # spaces / aliases like "<cmd>+<shift>+l".  Replace the token
        # only, leave everything else intact.
        import re
        merged["hotkey"] = re.sub(r"\bcmd\b", "ctrl", hk)

    # Reconcile engine list: keep the user's engines (in their order)
    # but append any default engines that aren't present, so newly-
    # shipped engines show up after an upgrade.
    user_engines = engines_from_dicts(data.get("engines") or [])
    default_list = default_engines()
    default_ids = {e.id for e in default_list}
    user_ids = {e.id for e in user_engines}
    for d in default_list:
        if d.id not in user_ids:
            user_engines.append(d)
    merged["engines"] = engines_to_dicts(user_engines)
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------

def set_hotkey(new_hotkey: str) -> Dict[str, Any]:
    """Update and persist the hotkey; returns the new full config."""
    cfg = load_config()
    cfg["hotkey"] = new_hotkey.strip()
    save_config(cfg)
    return cfg


def set_engine_enabled(engine_id: str, enabled: bool) -> Dict[str, Any]:
    """Toggle a single engine.  Returns the new full config."""
    cfg = load_config()
    engines = engines_from_dicts(cfg.get("engines") or [])
    found = False
    for e in engines:
        if e.id == engine_id:
            e.enabled = enabled
            found = True
            break
    if not found:
        # Not present in the saved list — try to add it from defaults
        for d in default_engines():
            if d.id == engine_id:
                d.enabled = enabled
                engines.append(d)
                break
    cfg["engines"] = engines_to_dicts(engines)
    save_config(cfg)
    return cfg


def reset_engines() -> Dict[str, Any]:
    """Reset the engine list to the factory defaults.  Returns the
    new full config."""
    cfg = load_config()
    cfg["engines"] = engines_to_dicts(default_engines())
    save_config(cfg)
    return cfg


__all__ = [
    "load_config",
    "save_config",
    "set_hotkey",
    "set_engine_enabled",
    "reset_engines",
    "config_path",
    "DEFAULT_HOTKEY",
    "DEFAULT_MAILTO",
    "DEFAULT_THRESHOLD",
    "APP_NAME",
]
