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
import re
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
# pynput 1.8+ GlobalHotKeys syntax: modifier keys must be wrapped in
# angle brackets (e.g. ``<cmd>``, ``<ctrl>``, ``<shift>``), and the
# ``cmd`` token means the macOS Command key only.  Use the OS-native
# modifier: macOS = cmd, Windows / Linux = ctrl.  The trailing key
# (``l`` here) is a single-character literal — no brackets needed.
DEFAULT_HOTKEY = "<cmd>+<shift>+l" if IS_DARWIN else "<ctrl>+<shift>+l"
DEFAULT_MAILTO = "syz@shiyangzheng.top"
DEFAULT_THRESHOLD = 0.85

# Valid values for the ``route_mode`` config key.  See
# ``main.lookup`` for the semantics of each.
VALID_ROUTE_MODES = ("auto", "search_always", "doi_always")
DEFAULT_ROUTE_MODE = "auto"


# ---------------------------------------------------------------------------
# Hotkey migration
# ---------------------------------------------------------------------------
# Older versions of citationHop stored the hotkey in the bare-token form
# (``cmd+shift+l``), which pynput 1.7 accepted but pynput 1.8+ rejects
# with ``ValueError: cmd`` (and ``ValueError: shift`` for any other bare
# modifier).  To keep existing users' config files valid, we normalise
# the hotkey to the angle-bracketed form on load.  Two transformations
# are applied:
#
# 1. Any bare modifier token (cmd / ctrl / alt / shift, plus their
#    ``_l`` / ``_r`` variants) is wrapped in angle brackets.
# 2. On non-Darwin platforms, ``cmd`` (bare or bracketed) is rewritten
#    to ``<ctrl>`` so the combo is valid on the host OS.
#
# After this passes, the string is guaranteed parseable by
# ``pynput.keyboard.HotKey.parse`` on the current platform.
_BARE_MODIFIER_RE = re.compile(
    r"(?<!<)\b(alt|ctrl|shift|cmd)(?:_[lr]|_gr)?\b(?!>)",
    re.IGNORECASE,
)
_BARE_CMD_RE = re.compile(r"(?<!<)\bcmd\b(?!>)", re.IGNORECASE)
_BRACKETED_CMD_RE = re.compile(r"<cmd>", re.IGNORECASE)


def _normalise_hotkey(s: str) -> str:
    """Return ``s`` rewritten into a pynput 1.8+ parseable form.

    Public-ish for testability.  Three passes:

    1. ``cmd`` → ``ctrl`` on non-Darwin (catches the bare-token case
       before the bracket pass).
    2. ``<cmd>`` → ``<ctrl>`` on non-Darwin (the bracketed form of the
       same legacy migration).  Without this, a user with a macOS-style
       ``<cmd>+<shift>+l`` in their config can't actually press the
       combo on Windows / Linux — there's no Cmd key there.
    3. Wrap any remaining bare modifier tokens in ``<...>``.
    """
    out = s.strip()
    if not IS_DARWIN:
        out = _BARE_CMD_RE.sub("ctrl", out)
        out = _BRACKETED_CMD_RE.sub("<ctrl>", out)
    out = _BARE_MODIFIER_RE.sub(r"<\1>", out)
    return out


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
        "route_mode": DEFAULT_ROUTE_MODE,
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

    # Validate route_mode — older configs may have a value we no
    # longer accept, or a typo.  Coerce unknown values back to the
    # default rather than crashing the app on load.  If we had to
    # repair anything, persist the cleaned form so subsequent loads
    # don't have to do this work (matches the hotkey-migration pattern
    # above).
    rm = merged.get("route_mode")
    if not isinstance(rm, str) or rm.lower() not in VALID_ROUTE_MODES:
        merged["route_mode"] = DEFAULT_ROUTE_MODE
        save_config(merged)
    else:
        normalised_rm = rm.lower()
        if normalised_rm != rm:
            merged["route_mode"] = normalised_rm
            save_config(merged)

    # Normalise a stale hotkey for the current platform: older versions
    # of citationHop stored ``cmd+shift+l`` (bare tokens), which pynput
    # 1.8+ rejects with ``ValueError: cmd``.  ``_normalise_hotkey``
    # wraps bare modifiers in angle brackets and rewrites ``cmd`` to
    # ``<ctrl>`` on non-Darwin so the saved string round-trips through
    # pynput's parser.
    if isinstance(merged.get("hotkey"), str):
        normalised = _normalise_hotkey(merged["hotkey"])
        if normalised != merged["hotkey"]:
            # Persist the migrated form so subsequent loads are
            # migration-free.  We only save when something actually
            # changed to avoid unnecessary disk writes.
            merged["hotkey"] = normalised
            save_config(merged)

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


def set_route_mode(mode: str) -> Dict[str, Any]:
    """Update and persist the routing mode.  Returns the new full
    config.  Unknown values are coerced to the default rather than
    raising — the menu calls this with raw radio-item indices and we
    don't want a config typo to brick the app."""
    cfg = load_config()
    m = (mode or "").lower().strip()
    cfg["route_mode"] = m if m in VALID_ROUTE_MODES else DEFAULT_ROUTE_MODE
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
    "set_route_mode",
    "reset_engines",
    "config_path",
    "DEFAULT_HOTKEY",
    "DEFAULT_MAILTO",
    "DEFAULT_THRESHOLD",
    "DEFAULT_ROUTE_MODE",
    "VALID_ROUTE_MODES",
    "APP_NAME",
    "_normalise_hotkey",
]
