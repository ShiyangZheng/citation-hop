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
                                            ✓ Google Scholar
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

Threading model
---------------
Two concerns shape how this module is wired up:

1.  pystray's :meth:`Icon.run` blocks the main thread on a native
    event loop (NSApp on macOS, the Win32 message pump on Windows,
    and a GTK main loop on Linux).

2.  pynput's :class:`GlobalHotKeys` runs a daemon thread with its
    own CFRunLoop.  The *callback* (here ``_on_hotkey``) is invoked
    on that listener thread, **not** on the main thread.

That second point is the one that bit us on macOS 15 (Apple
Silicon): pynput's ``Controller`` posts a synthetic CGEvent via
``CGEventPost`` to send the "Cmd+C" needed for the selection
capture.  When the post happens from inside the listener's own
CFRunLoop, macOS re-enters the HID event tap and the process dies
with ``SIGILL`` (the ``zsh: illegal hardware instruction`` reported
in the wild).  Two mitigations live here:

*   :func:`_on_hotkey` returns **immediately** and dispatches the
    actual work to a short-lived worker thread (``_HOTKEY_WORKER``).
    This keeps pynput's CFRunLoop responsive and decouples us from
    any re-entrancy.
*   :func:`platform_utils.simulate_copy` uses AppleScript (not
    pynput ``Controller``) on macOS, so we never post a synthetic
    CGEvent from inside the listener's CFRunLoop.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import webbrowser

# pystray pulls in a native UI backend at import time.  On macOS that's
# AppKit (fine), on Windows it's Win32 (fine), but on Linux it tries to
# open an X11 display via python-xlib.  CI runners and headless servers
# have no DISPLAY, so pystray's import raises DisplayNameError and
# breaks `from citation_hop import tray` — including pytest collection
# of tests/test_tray.py.  Degrade gracefully: keep the symbols defined
# (as None) so the rest of this module compiles, and expose
# _PYSTRAY_AVAILABLE so tests / callers can branch on it.
try:
    import pystray
    from pystray import MenuItem as Item, Menu
    _PYSTRAY_AVAILABLE = True
except Exception:  # noqa: BLE001 — backend init can raise many flavours
    pystray = None  # type: ignore[assignment]
    Item = None  # type: ignore[assignment]
    Menu = None  # type: ignore[assignment]
    _PYSTRAY_AVAILABLE = False

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
    IS_DARWIN,
    confirm,
    keystroke_label,
    load_tray_icon,
    notify,
    open_path,
)

LOG = logging.getLogger("citation_hop")

# Single dedicated worker thread for hotkey work.  We don't use a
# thread pool because the work is short-lived and we want backpressure
# to drop a hotkey press if the previous one hasn't finished yet
# (``is_alive`` check below).
_HOTKEY_WORKER: threading.Thread | None = None
_HOTKEY_LOCK = threading.Lock()


def _app_title() -> str:
    return f"citationHop {__version__}"


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------
#
# If a native crash (e.g. a Quartz / CGEventTap issue we haven't fully
# eliminated) still slips through, we want the user to see *why* the
# process died rather than a bare ``zsh: illegal hardware instruction``.
# Signal handlers can only run on the main Python thread, and only at
# the next bytecode boundary, so this is best-effort — but when it
# fires, the message is much more actionable than a raw SIGILL.
_FATAL_SIGNAL_HINTS: dict = {
    signal.SIGILL: (
        "SIGILL (illegal hardware instruction) — typically a macOS "
        "Quartz / CGEventTap issue.  Try: 1) grant Accessibility "
        "permission to the launching app (System Settings → Privacy & "
        "Security → Accessibility); 2) restart the app; 3) open an "
        "issue at https://github.com/ShiyangZheng/citation-hop/issues."
    ),
    signal.SIGSEGV: (
        "SIGSEGV (segmentation fault) — likely a native crash in pynput "
        "or pyperclip.  Try granting Accessibility permission (see "
        "above) and restarting."
    ),
}
# SIGBUS is a POSIX-only signal (BSD-style bus error).  On Windows the
# ``signal`` module doesn't expose it; referencing it at import time
# raises AttributeError and breaks `from citation_hop import tray`.
if hasattr(signal, "SIGBUS"):
    _FATAL_SIGNAL_HINTS[signal.SIGBUS] = (
        "SIGBUS — typically a memory-alignment / Quartz issue.  See "
        "Accessibility permission steps in SIGILL hint above."
    )


def _install_signal_handlers() -> None:
    """Install a friendly signal handler for the most common native
    crash signals.  Only meaningful on POSIX; on Windows the handlers
    are no-ops (use :func:`signal.signal` with ``SIGTERM`` etc.)."""
    for sig, hint in _FATAL_SIGNAL_HINTS.items():
        try:
            signal.signal(sig, _make_signal_handler(sig, hint))
        except (ValueError, OSError, AttributeError):
            # Not all signals are installable on every platform
            # (e.g. SIGILL on Windows, or when not on the main thread).
            pass


def _make_signal_handler(sig, hint: str):
    def _handler(signo, _frame):
        try:
            if sys is not None and sys.stderr is not None:
                sys.stderr.write(
                    f"\n[citationHop] caught signal {signo}: {hint}\n"
                )
                sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        # Restore default behaviour (core dump / exit 128+signo) so
        # we still surface a real crash to the user, just with context.
        try:
            signal.signal(signo, signal.SIG_DFL)
            os.kill(os.getpid(), signo)
        except Exception:  # noqa: BLE001
            os._exit(128 + signo)
    return _handler


# ---------------------------------------------------------------------------
# IS_TRUSTED / Accessibility check
# ---------------------------------------------------------------------------

_TRUST_CHECK_DELAY_S = 0.4  # pynput sets IS_TRUSTED in its own thread


def _check_trust_and_warn(listener) -> None:
    """If the pynput listener didn't gain Accessibility trust, pop a
    one-time notification.  Logs unconditionally so the issue shows
    up in stdout even if the notification is dismissed."""
    # pynput populates IS_TRUSTED asynchronously inside its CFRunLoop
    # thread; give it a moment before we look.
    time.sleep(_TRUST_CHECK_DELAY_S)
    trusted = getattr(listener, "IS_TRUSTED", None)
    if trusted is False:
        LOG.warning(
            "Process is not trusted for Accessibility (Input Monitoring). "
            "Global hotkey will not fire.  Grant access in "
            "System Settings → Privacy & Security → Accessibility, "
            "then restart citationHop."
        )
        if IS_DARWIN:
            try:
                import subprocess as _sp
                _sp.Popen(
                    [
                        "open",
                        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                    ]
                )
            except Exception:  # noqa: BLE001
                pass
            notify(
                _app_title(),
                "Accessibility permission required",
                subtitle=(
                    "Open System Settings → Privacy & Security → Accessibility, "
                    "grant access, then restart citationHop."
                ),
            )


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
    #
    # The handler runs on pynput's listener thread.  We dispatch the
    # actual work (get_selection, lookup, webbrowser.open) onto a
    # short-lived worker thread so the listener's CFRunLoop can
    # return to its loop immediately.  This is the keystone of the
    # CGEventTap re-entrancy fix: pynput's CFRunLoop must NEVER make
    # a blocking call (pyperclip, time.sleep, osascript, Controller)
    # inside ``_on_hotkey``.

    def _on_hotkey(self) -> None:
        # Synchronous part: just enqueue, never block.
        if not self.enabled:
            return
        with _HOTKEY_LOCK:
            global _HOTKEY_WORKER
            if _HOTKEY_WORKER is not None and _HOTKEY_WORKER.is_alive():
                # Drop a duplicate press: the previous lookup is still
                # running.  Prevents accidental re-entry if AppleScript
                # or pyperclip briefly stalls.
                LOG.debug("Dropping hotkey press: previous worker still running")
                return
            t = threading.Thread(
                target=self._do_hotkey_work,
                name="citationHop-hotkey",
                daemon=True,
            )
            _HOTKEY_WORKER = t
        t.start()

    def _do_hotkey_work(self) -> None:
        """The actual work, on a worker thread (not pynput's listener)."""
        try:
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
                       subtitle="Select some text first, then press the hotkey.")
                return
            if status == "not_citation":
                notify(_app_title(), "Doesn't look like a citation",
                       subtitle="Try selecting a full reference entry.")
                return

            url = result["url"]
            engine_used = result.get("engine_used")

            if status == "doi" and result["doi"]:
                copy_to_clipboard(result["doi"])
                notify(
                    _app_title(),
                    "DOI: " + result["doi"],
                    subtitle=f"Opening via {engine_used} · DOI copied",
                )
            else:
                notify(
                    _app_title(),
                    f"No DOI found — opening {engine_used}",
                    subtitle=url[:80] + ("…" if len(url) > 80 else ""),
                )

            try:
                webbrowser.open(url)
            except Exception as e:  # noqa: BLE001
                LOG.exception("Failed to open URL")
                notify(_app_title(), "Browser error", str(e))
        except Exception:  # noqa: BLE001
            # Last-ditch: never let a worker-thread exception take down
            # the listener.  Log it; the user might lose one lookup.
            LOG.exception("Unhandled exception in hotkey worker")

    # ---- entry point ----------------------------------------------------

    def run(self) -> int:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        _install_signal_handlers()
        # Register the hotkey in a background thread *before* the icon
        # loop takes over, so the binding is live as soon as the icon
        # appears.
        self._register_hotkey()
        # Spawn the trust check (it sleeps briefly so pynput has time
        # to populate IS_TRUSTED, then warns if needed).
        threading.Thread(
            target=_check_trust_and_warn,
            args=(self._listener,),
            name="citationHop-trust-check",
            daemon=True,
        ).start()
        self.icon.run()
        return 0


def main() -> int:
    return CitationHopTray().run()


__all__ = ["CitationHopTray", "main"]
