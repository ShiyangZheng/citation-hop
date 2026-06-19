"""rumps-based macOS menu-bar UI for citationHop.

Lives in its own module so that ``citation_hop.main.lookup`` can be
imported and unit-tested without pulling in rumps / PyObjC.
"""

from __future__ import annotations

import logging
import subprocess
import webbrowser

import rumps

from . import __version__
from .clipboard import copy_to_clipboard, get_selection
from .config import config_path, load_config, set_hotkey
from .main import lookup

LOG = logging.getLogger("citation_hop")

_APP_TITLE = f"citationHop {__version__}"


class CitationHopApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(_APP_TITLE, title="📎", quit_button=None)
        self.cfg = load_config()
        self.enabled = True
        # Use a stable reference for the hotkey menu item so we can
        # update its title in place when the user changes the hotkey.
        self._hotkey_item = rumps.MenuItem(
            "Hotkey: " + self.cfg["hotkey"],
            callback=self._change_hotkey,
        )
        self.menu = [
            rumps.MenuItem("Enabled", callback=self._toggle_enabled),
            None,
            self._hotkey_item,
            rumps.MenuItem("Open config file", callback=self._open_config),
            None,
            rumps.MenuItem(f"citationHop {__version__}", callback=None),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        # rumps doesn't auto-tick menu items; manage the state explicitly.
        self.menu["Enabled"].state = True
        self._register_hotkey(self.cfg["hotkey"])

    # ---- menu callbacks --------------------------------------------------

    def _toggle_enabled(self, sender) -> None:
        self.enabled = not self.enabled
        sender.state = self.enabled
        if not self.enabled:
            rumps.notification(
                _APP_TITLE,
                "Disabled",
                "Press the menu item again to re-enable.",
            )

    def _change_hotkey(self, _sender) -> None:
        window = rumps.Window(
            message="Enter a new hotkey (pynput GlobalHotKeys syntax):\n"
            "e.g.  cmd+shift+l   /   cmd+alt+d   /   ctrl+shift+x",
            title="Change hotkey",
            default_text=self.cfg["hotkey"],
            ok="Save",
            cancel="Cancel",
            dimensions=(360, 24),
        )
        window.run()
        if window.clicked:
            new = (window.text or "").strip()
            if new:
                self.cfg = set_hotkey(new)
                self._hotkey_item.title = "Hotkey: " + self.cfg["hotkey"]
                try:
                    self._register_hotkey(self.cfg["hotkey"])
                except Exception as e:  # noqa: BLE001
                    rumps.alert("Failed to register hotkey", str(e))

    def _open_config(self, _sender) -> None:
        subprocess.Popen(["open", str(config_path())])

    # ---- hotkey plumbing ------------------------------------------------

    def _register_hotkey(self, hotkey: str) -> None:
        # We always (re)create the listener; pynput's GlobalHotKeys does
        # not support hot-swapping a single binding.
        if hasattr(self, "_listener") and self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # pragma: no cover
                pass
        from pynput import keyboard  # local import: avoid cost at module load

        self._listener = keyboard.GlobalHotKeys({hotkey: self._on_hotkey})
        self._listener.daemon = True
        self._listener.start()

    # ---- the actual hotkey handler --------------------------------------

    def _on_hotkey(self) -> None:
        if not self.enabled:
            return
        try:
            text = get_selection()
        except Exception as e:  # noqa: BLE001
            LOG.exception("get_selection failed")
            rumps.notification(_APP_TITLE, "Selection error", str(e))
            return

        result = lookup(text, mailto=self.cfg.get("mailto"))
        status = result["status"]

        if status == "empty":
            rumps.notification(
                _APP_TITLE,
                "Nothing selected",
                "Select some text first, then press the hotkey.",
            )
            return
        if status == "not_citation":
            rumps.notification(
                _APP_TITLE,
                "Doesn't look like a citation",
                "Try selecting a full reference entry.",
            )
            return

        url = result["url"]
        if status == "doi" and result["doi"]:
            copy_to_clipboard(result["doi"])
            rumps.notification(
                _APP_TITLE,
                "DOI: " + result["doi"],
                "Opening in browser · DOI copied to clipboard",
            )
        else:
            rumps.notification(
                _APP_TITLE,
                "No DOI found",
                "Opening Google Scholar search.",
            )

        try:
            webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            LOG.exception("Failed to open URL")
            rumps.notification(_APP_TITLE, "Browser error", str(e))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    CitationHopApp().run()
    return 0
