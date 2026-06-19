"""User-facing configuration for citationHop.

We persist a single JSON file under macOS's standard per-user app
support directory:

    ~/Library/Application Support/citationHop/config.json

If the file does not exist (first run), we create it with defaults.
If it exists but is malformed, we back it up and start fresh — we
never crash the app over a config error.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

APP_NAME = "citationHop"
DEFAULT_HOTKEY = "cmd+shift+l"  # pynput GlobalHotKeys syntax
DEFAULT_FALLBACK = "scholar"     # currently only "scholar" is supported
DEFAULT_MAILTO = "syz@shiyangzheng.top"
DEFAULT_THRESHOLD = 0.85


def _config_dir() -> Path:
    """Return (and create) the per-user config directory."""
    base = Path.home() / "Library" / "Application Support" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return _config_dir() / "config.json"


def _defaults() -> dict:
    return {
        "hotkey": DEFAULT_HOTKEY,
        "fallback_engine": DEFAULT_FALLBACK,
        "mailto": DEFAULT_MAILTO,
        "similarity_threshold": DEFAULT_THRESHOLD,
    }


def load_config() -> dict:
    """Load config, falling back to defaults on missing / bad file."""
    path = config_path()
    if not path.exists():
        cfg = _defaults()
        save_config(cfg)
        return cfg

    try:
        with path.open("r", encoding="utf-8") as f:
            data: Any = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Back up the broken file and start fresh.
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

    # Merge defaults so newly-added keys still work after upgrades.
    merged = _defaults()
    merged.update({k: v for k, v in data.items() if k in merged})
    return merged


def save_config(cfg: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def set_hotkey(new_hotkey: str) -> dict:
    """Update and persist the hotkey; returns the new full config."""
    cfg = load_config()
    cfg["hotkey"] = new_hotkey.strip()
    save_config(cfg)
    return cfg


__all__ = [
    "load_config",
    "save_config",
    "set_hotkey",
    "config_path",
    "DEFAULT_HOTKEY",
    "DEFAULT_FALLBACK",
    "DEFAULT_MAILTO",
    "DEFAULT_THRESHOLD",
]
