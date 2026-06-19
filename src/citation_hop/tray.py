"""Cross-platform menu-bar / system-tray UI for citationHop.

Replaces the v1.0 ``app.py`` (rumps-based, macOS-only) with a
``pystray``-based UI that works on macOS, Windows, and Linux.  The
hotkey, selection capture, and lookup pipeline are unchanged.

The menu structure:

    [icon] Enabled                          ✓
    --------
    [icon] Hotkey: <cmd>+<shift>+l      >   Change hotkey…
    [icon] Search engines               >   ✓ Crossref
                                            ✓ doi.org
                                            ☐ Sci-Hub
                                            ☐ Google Scholar
                                            ✓ Semantic Scholar
                                            ...
                                            --------
                                            Reset to defaults
                                            Open engines in config
    --------
    [icon] Open config file
    [icon] About citationHop
    --------
    [icon] Quit
"""

from __future__ import annotations

import logging
import subprocess
import threading
import webbrowser

import pystray
from pystray import MenuItem as Item, Menu

from . import __version__
from .clipboard import copy_to_clipboard, get_selection
from .config import (
    config_path,
    load_config,
    reset_engines,
    set_engine_enabled,
    set_hotkey,
)
from .engines import (
    STAGE_DOI_RESOLVER,
    STAGE_DOI_URL,
    STAGE_SEARCH_URL,
    by_stage,
    engines_from_dicts,
    sort_by_order,
)
from .main import lookup
from .platform_utils import (
    confirm,
    keystroke_label,
    load_tray_icon,
    notify,
    open_path,
)

LOG = logging.getLogger("citation_hop")


def _app_title() -> str:
    return f"citationHop {__version__}"


class CitationHopTray:
    """The tray-icon application.  One instance per process."""

    def __init__(self) -> None:
        self.cfg = load_config()
        self.enabled = True
        self.icon: pystray.Icon = self._build_icon()

    # ---- icon + menu construction ---------------------------------------

    def _build_icon(self) -> pystray.Icon:
        icon_image = load_tray_icon()
        icon = pystray.Icon(
            name="citationHop",
            title=_app_title(),
            icon=icon_image,
            menu=self._build_menu(),
        )
        # Register the hotkey in a background thread once the icon is up.
        # We can't pynput from a non-main thread on macOS in some cases,
        # so pystray's own run() loop is the right place.
        return icon

    def _build_menu(self) -> Menu:
        return Menu(
            Item("Enabled", self._on_toggle_enabled, checked=lambda i: self.enabled),
            Menu.SEPARATOR,
            Item(
                "Hotkey: " + keystroke_label(self.cfg["hotkey"]),
                Menu(
                    Item("Change hotkey…", self._on_change_hotkey),
                ),
            ),
            Item(
                "Search engines",
                Menu(
                    *self._engine_menu_items(),
                    Menu.SEPARATOR,
                    Item("Reset to defaults", self._on_reset_engines),
                    Item("Edit in config file…", self._on_open_config),
                ),
            ),
            Menu.SEPARATOR,
            Item("Open config file", self._on_open_config),
            Item(f"About {_app_title()}", self._on_about),
            Menu.SEPARATOR,
            Item("Quit", self._on_quit),
        )

    def _engine_menu_items(self) -> list:
        items: list = []
        engines = engines_from_dicts(self.cfg.get("engines") or [])

        def _make_checker(engine_id: str):
            def _is_checked(_item) -> bool:
                eng = engines_from_dicts(self.cfg.get("engines") or [])
                return any(e.id == engine_id and e.enabled for e in eng)
            return _is_checked

        def _make_toggle(engine_id: str):
            def _toggle(_item) -> None:
                eng = engines_from_dicts(self.cfg.get("engines") or [])
                target = next((e for e in eng if e.id == engine_id), None)
                if target is None:
                    return
                self.cfg = set_engine_enabled(engine_id, not target.enabled)
                self._refresh_menu()
            return _toggle

        for stage, label in (
            (STAGE_DOI_RESOLVER, "DOI resolvers"),
            (STAGE_DOI_URL, "DOI redirect URLs"),
            (STAGE_SEARCH_URL, "Search engines"),
        ):
            stage_engines = sort_by_order(
                e for e in engines if e.stage == stage
            )
            if not stage_engines:
                continue
            items.append(Item(label, None, enabled=False))  # subheader
            for e in stage_engines:
                items.append(
                    Item(
                        e.name,
                        _make_toggle(e.id),
                        checked=_make_checker(e.id),
                        radio=False,
                    )
                )
        if not items:
            items.append(Item("(no engines configured)", None, enabled=False))
        return items

    # ---- menu refresh ---------------------------------------------------

    def _refresh_menu(self) -> None:
        """Rebuild the menu after a config change.  pystray's Icon
        stores its menu on a private attribute, but the public API
        is to set ``icon.icon`` and ``icon.menu`` and re-emit."""
        try:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            LOG.exception("Failed to refresh menu")

    # ---- callbacks ------------------------------------------------------

    def _on_toggle_enabled(self, _icon, _item) -> None:
        self.enabled = not self.enabled
        if not self.enabled:
            notify(_app_title(), "Disabled — pick this menu item again to re-enable.")

    def _on_change_hotkey(self, _icon, _item) -> None:
        # pystray doesn't ship a native text-input dialog; we reuse the
        # platform_utils.confirm() for OK/Cancel and ask the user to
        # type the new key in the open dialog (or in the config file).
        # For a quick UX, we just pop a message telling them what to
        # do, then open the config file in their default editor.
        msg = (
            f"Current hotkey: {keystroke_label(self.cfg['hotkey'])}\n\n"
            "To change it, edit the 'hotkey' field in the config file "
            "using pynput GlobalHotKeys syntax.  Modifiers (cmd, ctrl, "
            "shift, alt, …) must be wrapped in angle brackets; the "
            "trailing key is a single character.  Examples:\n"
            "  <cmd>+<shift>+l   <ctrl>+<alt>+d   <ctrl>+<shift>+x\n\n"
            "Note: on macOS use <cmd>; on Windows / Linux use <ctrl>.\n\n"
            "Open the config file now?"
        )
        if confirm("Change hotkey", msg, default=True):
            self._on_open_config(None, None)

    def _on_reset_engines(self, _icon, _item) -> None:
        if not confirm(
            "Reset search engines",
            "Restore the default list of search engines?\n"
            "Custom engines you've added will be removed.",
            default=False,
        ):
            return
        self.cfg = reset_engines()
        self._refresh_menu()
        notify(_app_title(), "Search engines reset to defaults.")

    def _on_open_config(self, _icon, _item) -> None:
        open_path(config_path())

    def _on_about(self, _icon, _item) -> None:
        msg = (
            f"citationHop {__version__}\n\n"
            "Select a citation, press the hotkey, get the paper.\n\n"
            f"Config: {config_path()}"
        )
        confirm("About citationHop", msg, default=True)

    def _on_quit(self, _icon, _item) -> None:
        try:
            if hasattr(self, "_listener") and self._listener is not None:
                self._listener.stop()
        except Exception:  # noqa: BLE001
            pass
        self.icon.stop()

    # ---- hotkey plumbing ------------------------------------------------

    def _register_hotkey(self) -> None:
        from pynput import keyboard  # local: avoid cost at import

        if hasattr(self, "_listener") and self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # pragma: no cover
                pass
        self._listener = keyboard.GlobalHotKeys({self.cfg["hotkey"]: self._on_hotkey})
        self._listener.daemon = True
        self._listener.start()

    # ---- hotkey handler -------------------------------------------------

    def _on_hotkey(self) -> None:
        if not self.enabled:
            return
        try:
            text = get_selection()
        except Exception as e:  # noqa: BLE001
            LOG.exception("get_selection failed")
            notify(_app_title(), "Selection error", str(e))
            return

        engines = engines_from_dicts(self.cfg.get("engines") or [])
        result = lookup(text, engines=engines, mailto=self.cfg.get("mailto"))
        status = result["status"]

        if status == "empty":
            notify(_app_title(), "Nothing selected",
                   "Select some text first, then press the hotkey.")
            return
        if status == "not_citation":
            notify(_app_title(), "Doesn't look like a citation",
                   "Try selecting a full reference entry.")
            return

        url = result["url"]
        engine_used = result.get("engine_used")

        if status == "doi" and result["doi"]:
            copy_to_clipboard(result["doi"])
            notify(
                _app_title(),
                "DOI: " + result["doi"],
                f"Opening via {engine_used} · DOI copied",
            )
        else:
            notify(
                _app_title(),
                f"No DOI found — opening {engine_used}",
                url[:80] + ("…" if len(url) > 80 else ""),
            )

        try:
            webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            LOG.exception("Failed to open URL")
            notify(_app_title(), "Browser error", str(e))

    # ---- entry point ----------------------------------------------------

    def run(self) -> int:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # Register the hotkey in a background thread *before* the icon
        # loop takes over, so the binding is live as soon as the icon
        # appears.
        self._register_hotkey()
        self.icon.run()
        return 0


def main() -> int:
    return CitationHopTray().run()


__all__ = ["CitationHopTray", "main"]
