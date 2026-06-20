"""Cross-platform UI primitives for citationHop.

This module hides every OS-specific detail (rumps, osascript, Win32
MessageBox, PowerShell toast, xdg-open, etc.) behind small,
testable functions.  The rest of the app only ever calls these.

* :func:`confirm`           — modal OK / Cancel dialog, returns bool
* :func:`notify`            — non-blocking desktop notification
* :func:`simulate_copy`     — fire a ``Cmd+C`` / ``Ctrl+C`` keystroke
* :func:`open_path`         — open a file / URL with the OS default app
* :func:`load_tray_icon`    — return a PIL Image for the tray icon
* :func:`keystroke_label`   — pretty-print a pynput combo for menus

Design notes
------------
* macOS notifications:  ``osascript display notification``.  Reliable,
  zero extra deps, doesn't require PyObjC for this one call.
* Windows notifications: ``plyer`` (preferred) → PowerShell BurntToast
  / WinRT toast → silent no-op.
* Windows dialog:       ``ctypes.windll.user32.MessageBoxW`` — native,
  no extra deps, no Tk needed.
* Linux:                best-effort, fall through silently if the
  platform doesn't have a working backend.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import urllib.parse
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("citation_hop.platform")

IS_DARWIN = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

SYSTEM = platform.system()  # 'Darwin' | 'Windows' | 'Linux' | ...

_COPY_DELAY_S = 0.12


# ---------------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------------

def get_package_dir() -> Path:
    """Return the directory containing the citation_hop package."""
    return Path(__file__).resolve().parent


def get_assets_dir() -> Path:
    """Return the path to ``assets/`` next to the package."""
    return get_package_dir() / "assets"


def load_tray_icon():
    """Return a PIL Image for the tray icon.  Generates a tiny fallback
    on the fly if the bundled PNG is missing, so the app never fails to
    start over a missing icon asset."""
    from PIL import Image, ImageDraw  # type: ignore

    icon_path = get_assets_dir() / "icon.png"
    if icon_path.exists():
        try:
            return Image.open(icon_path)
        except Exception:  # pragma: no cover
            LOG.debug("Failed to open %s; using fallback", icon_path)

    # Fallback: 64x64 blue square with a white "C".
    img = Image.new("RGBA", (64, 64), (30, 144, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        # Try a TrueType font if available; otherwise PIL falls back
        # to its own bitmap font.
        from PIL import ImageFont  # type: ignore
        font = ImageFont.load_default()
    except Exception:  # pragma: no cover
        font = None
    draw.text((20, 18), "C", fill="white", font=font)
    return img


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def confirm(title: str, message: str, *, default: bool = True) -> bool:
    """Show a modal OK / Cancel dialog, return True iff the user
    clicked OK.  Always returns the *default* on platforms where we
    can't actually pop a dialog, so a failed dialog never blocks the
    user."""
    try:
        if IS_DARWIN:
            return _confirm_macos(title, message, default=default)
        if IS_WIN:
            return _confirm_windows(title, message, default=default)
        if IS_LINUX:
            return _confirm_linux(title, message, default=default)
    except Exception:  # noqa: BLE001
        LOG.exception("confirm() failed on %s", SYSTEM)
    return default


def _confirm_macos(title: str, message: str, *, default: bool) -> bool:
    """AppleScript display dialog.  We must catch the User canceled
    error (subprocess exits non-zero) and treat it as Cancel."""
    default_button = "OK" if default else "Cancel"
    # Escape double-quotes in user-provided text.
    safe_title = title.replace('"', "'")
    safe_msg = message.replace('"', "'")
    script = (
        f'try\n'
        f'  display dialog "{safe_msg}" '
        f'buttons {{"Cancel", "OK"}} '
        f'default button "{default_button}" '
        f'with title "{safe_title}"\n'
        f'  return "ok"\n'
        f'on error\n'
        f'  return "cancel"\n'
        f'end try\n'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return default
    return "ok" in (result.stdout or "")


def _confirm_windows(title: str, message: str, *, default: bool) -> bool:
    """Native Win32 MessageBoxW via ctypes.  No extra deps, no Tk."""
    import ctypes  # local: only Windows imports this

    MB_OKCANCEL = 0x00000001
    MB_ICONINFORMATION = 0x00000040
    IDOK = 1
    user32 = ctypes.windll.user32
    # MB_DEFAULT_DESKTOP_ONLY etc. can be added; we keep it simple.
    flags = MB_OKCANCEL | MB_ICONINFORMATION
    if default:
        flags |= 0x00000000  # default = first button (OK)
    ret = user32.MessageBoxW(0, message, title, flags)
    return ret == IDOK


def _confirm_linux(title: str, message: str, *, default: bool) -> bool:
    """Best-effort: try ``zenity`` (GNOME) then ``kdialog`` (KDE)."""
    for cmd in (["zenity", "--question", f"--title={title}", f"--text={message}"],
                ["kdialog", "--yesno", message, "--title", title]):
        if not shutil.which(cmd[0]):
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            # zenity: 0 = yes, 1 = no.  kdialog: 0 = yes, 1 = no.
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return default


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(title: str, message: str, subtitle: Optional[str] = None, *_args, **_kwargs) -> None:
    """Show a non-blocking desktop notification.  Never raises.

    Both ``notify("t", "m", "s")`` (positional subtitle, legacy) and
    ``notify("t", "m", subtitle="s")`` (keyword form, preferred) are
    accepted so older callers don't break.
    """
    try:
        if IS_DARWIN:
            _notify_macos(title, message, subtitle=subtitle)
        elif IS_WIN:
            _notify_windows(title, message, subtitle=subtitle)
        elif IS_LINUX:
            _notify_linux(title, message, subtitle=subtitle)
    except Exception:  # noqa: BLE001
        LOG.debug("notify() failed on %s", SYSTEM, exc_info=True)


def _notify_macos(title: str, message: str, *, subtitle: Optional[str]) -> None:
    safe_title = title.replace('"', "'")
    safe_msg = message.replace('"', "'")
    sub = f' subtitle "{subtitle.replace(chr(34), chr(39))}"' if subtitle else ""
    script = (
        f'display notification "{safe_msg}" with title "{safe_title}"{sub}'
    )
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, timeout=5, check=False,
    )


def _notify_windows(title: str, message: str, *, subtitle: Optional[str]) -> None:
    """Try plyer first (cross-platform), then PowerShell BurntToast /
    WinRT toast.  Silent no-op if nothing is available."""
    try:
        from plyer import notification as _plyer  # type: ignore
        _plyer.notify(title=title, message=message, app_name="citationHop", timeout=5)
        return
    except Exception:  # noqa: BLE001
        pass

    # Fallback: PowerShell BurntToast.  Only works if the user has it
    # installed; we don't bundle it.
    ps = (
        "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null;"
        "[reflection.assembly]::loadwithpartialname('System.Drawing') | Out-Null;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(5000, '{_ps_escape(title)}', '{_ps_escape(message)}', "
        "[System.Windows.Forms.ToolTipIcon]::Info);"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _ps_escape(s: str) -> str:
    return s.replace("'", "''")


def _notify_linux(title: str, message: str, *, subtitle: Optional[str]) -> None:
    """``notify-send`` if available."""
    if not shutil.which("notify-send"):
        return
    args = ["notify-send", "--app-name=citationHop", title, message]
    if subtitle:
        args.insert(2, subtitle)
    try:
        subprocess.run(args, capture_output=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------
# Keystroke simulation
# ---------------------------------------------------------------------------

def simulate_copy() -> None:
    """Simulate a copy keystroke (``Cmd+C`` on macOS, ``Ctrl+C`` elsewhere).

    Safe to call on any platform: a no-op if the backend can't be
    loaded.

    Platform notes
    --------------
    **macOS** — we deliberately route through AppleScript
    (``osascript -e 'tell application "System Events" to keystroke "c"
    using command down'``) instead of pynput's ``Controller``.  This
    matters because pynput's ``Controller`` posts a synthetic CGEvent
    via ``CGEventPost``, and if we're called from inside a pynput
    keyboard listener's own CFRunLoop (which is exactly what happens
    inside the hotkey handler), macOS sees the event as coming from
    this process's HID event tap.  On macOS 15 / Apple Silicon that
    re-entrancy triggers a Mach exception (commonly surfaced as
    ``SIGILL`` / ``zsh: illegal hardware instruction``) when the
    synthetic Cmd+C re-enters our own event tap.  AppleScript's
    ``keystroke`` is delivered through a different code path (System
    Events → WindowServer) and is not subject to the same re-entrancy.

    **Windows / Linux** — pynput's ``Controller`` is the right tool.
    It uses ``SendInput`` / ``XTestFakeKeyEvent`` respectively, neither
    of which re-enters the listener.
    """
    if IS_DARWIN:
        _simulate_copy_applescript()
        return

    try:
        from pynput.keyboard import Controller, Key  # type: ignore
    except Exception:  # pragma: no cover
        return

    # pynput.keyboard imports cleanly on most hosts, but Controller()
    # instantiation can still fail on stripped CI images (no X server,
    # missing libgtk, etc.).  Treat any failure as a silent no-op —
    # the hotkey will still fire, just without a fresh selection
    # capture.
    try:
        kb = Controller()
        kb.press(Key.ctrl)
        kb.press("c")
        kb.release("c")
        kb.release(Key.ctrl)
    except Exception:  # noqa: BLE001
        LOG.debug("pynput Controller failed on %s", SYSTEM, exc_info=True)


def _simulate_copy_applescript() -> None:
    """Send a synthetic Cmd+C via AppleScript System Events.

    Used on macOS as the primary path (not just a fallback) to avoid
    the CGEventTap re-entrancy crash described in :func:`simulate_copy`.

    Requires the **Automation → System Events** permission for the
    calling process (terminal or bundled .app).  On macOS 13+ this is
    requested automatically the first time ``osascript`` invokes
    System Events; the user just has to click Allow.  If denied, this
    is a silent no-op (the ``subprocess.run`` returns non-zero) and
    the user's selection won't reach the clipboard — the lookup will
    fall through to an empty / not-citation result with a friendly
    notification, not a crash.

    **Zotero quirk** (2026-06-20): when the user has selected text
    inside Zotero's PDF reader and the hotkey fires while *another*
    app (terminal, browser, etc.) is frontmost, sending Cmd+C via
    System Events without first bringing Zotero to the front causes
    the keystroke to reach the frontmost app instead of Zotero.  In
    that case the clipboard either keeps its previous contents or
    gets a non-selection string, and citationHop ends up opening the
    *previously copied* citation rather than the one the user just
    selected.  We fix this by calling ``activate`` on Zotero first
    when Zotero is detected, ensuring Zotero handles the keystroke.
    The whole activate-then-copy takes ~250 ms — slow enough that we
    bump the downstream sleep in ``clipboard.get_selection``.
    """
    if not IS_DARWIN:
        return
    # Pre-step: bring Zotero to the front so it handles Cmd+C.
    # We only do this when Zotero is the source app; for other apps
    # (browser, PDF viewer, text editor) the existing behaviour is
    # correct.  is_zotero_installed is cheap (cached for 60 s).
    if is_zotero_installed():
        activate_script = 'tell application "Zotero" to activate'
        try:
            subprocess.run(
                ["osascript", "-e", activate_script],
                capture_output=True, timeout=1, check=False,
            )
            # Tiny settle delay so Zotero finishes its focus transition
            # before we fire Cmd+C.  Without this, on a slow Mac the
            # keystroke can land during the focus animation and get
            # swallowed by the wrong app.
            time.sleep(0.05)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    script = (
        'tell application "System Events" to keystroke "c" using command down'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):  # pragma: no cover
        LOG.debug("AppleScript Cmd+C failed (osascript missing or timed out)")


def get_selection_via_copy() -> str:
    """Snapshot the clipboard, simulate a copy, return the new contents.

    Restores the original clipboard contents when done.
    """
    import pyperclip  # local — Windows may not have it on PATH

    original = ""
    try:
        original = pyperclip.paste()
    except Exception:  # pragma: no cover
        pass

    try:
        try:
            pyperclip.copy("")
        except Exception:  # pragma: no cover
            pass
        simulate_copy()
        time.sleep(_COPY_DELAY_S)
        try:
            return pyperclip.paste() or ""
        except Exception:  # pragma: no cover
            return ""
    finally:
        try:
            pyperclip.copy(original)
        except Exception:  # pragma: no cover
            LOG.debug("Failed to restore original clipboard contents")


def copy_to_clipboard(text: str) -> None:
    """Public helper: put *text* on the clipboard."""
    import pyperclip
    pyperclip.copy(text)


# ---------------------------------------------------------------------------
# Open file / URL
# ---------------------------------------------------------------------------

def open_path(path: str | Path) -> None:
    """Open a file / URL with the OS default handler."""
    path = str(path)
    try:
        if IS_DARWIN:
            subprocess.Popen(["open", path])
        elif IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        LOG.exception("open_path(%s) failed", path)


# ---------------------------------------------------------------------------
# Keystroke formatting
# ---------------------------------------------------------------------------

# pynput Key names -> platform labels.  Keys not in this map are passed
# through with light prettification (capitalize single chars, friendly
# names for common special keys).
_MODS_MAC = {
    "cmd": "⌘", "cmd_l": "⌘", "cmd_r": "⌘",
    "ctrl": "⌃", "ctrl_l": "⌃", "ctrl_r": "⌃",
    "alt": "⌥", "alt_l": "⌥", "alt_r": "⌥", "option": "⌥",
    "shift": "⇧", "shift_l": "⇧", "shift_r": "⇧",
}

# Windows uses textual labels (no glyph convention).  Linux mirrors this.
_MODS_TEXT = {
    "cmd": "Win", "cmd_l": "Win", "cmd_r": "Win",
    "ctrl": "Ctrl", "ctrl_l": "Ctrl", "ctrl_r": "Ctrl",
    "alt": "Alt", "alt_l": "Alt", "alt_r": "Alt", "option": "Alt",
    "shift": "Shift", "shift_l": "Shift", "shift_r": "Shift",
}

_PRETTY_KEYS = {
    "esc": "Esc", "escape": "Esc",
    "space": "Space", "spacebar": "Space",
    "tab": "Tab",
    "enter": "Enter", "return": "Enter",
    "backspace": "Backspace",
    "delete": "Del", "del": "Del",
    "home": "Home", "end": "End",
    "page_up": "PageUp", "pageup": "PageUp",
    "page_down": "PageDown", "pagedown": "PageDown",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "caps_lock": "CapsLock", "capslock": "CapsLock",
    "num_lock": "NumLock", "numlock": "NumLock",
    "scroll_lock": "ScrollLock",
}


def keystroke_label(combination: str) -> str:
    """Render a pynput-style hotkey combo in a platform-readable form.

    Examples (macOS):
      ``<cmd>+<shift>+d``   →  ``⌘⇧D``
      ``<ctrl>+<alt>+<f5>`` →  ``⌃⌥F5``

    Examples (Windows):
      ``<cmd>+<shift>+d``   →  ``Win+Shift+D``
      ``<ctrl>+<alt>+<f5>`` →  ``Ctrl+Alt+F5``

    Examples (Linux):
      ``<cmd>+<shift>+d``   →  ``Win+Shift+D``
      ``<ctrl>+<alt>+<f5>`` →  ``Ctrl+Alt+F5``

    On macOS modifiers are joined with no separator (Apple Human
    Interface Guidelines convention).  On Windows / Linux they are
    joined with ``+``.

    Note: pynput's ``<cmd>`` token maps to the macOS Command key on
    macOS, the Windows / Super key on Windows, and the Super key on
    Linux.  We render the binding **literally** so users see what
    pynput will actually do — there is no silent "translate cmd to
    ctrl" step.  If a user wants a portable binding they should pick
    ``<ctrl>`` explicitly in their config.

    Unknown keys pass through with light cleanup (capitalised,
    ``<f1>`` → ``F1``).  Empty / ``None`` input returns ``""``.
    """
    if not combination:
        return ""

    tokens = [t.strip() for t in combination.split("+") if t.strip()]
    if not tokens:
        return ""

    if IS_DARWIN:
        mods = _MODS_MAC
        sep = ""
    else:
        mods = _MODS_TEXT
        sep = "+"

    out: list[str] = []
    for tok in tokens:
        name = tok.strip("<>").strip().lower()
        if not name:
            continue
        if name in mods:
            out.append(mods[name])
            continue
        if name in _PRETTY_KEYS:
            out.append(_PRETTY_KEYS[name])
            continue
        # Single character: uppercase.  Function keys: capitalise ("f1" -> "F1").
        if len(name) == 1:
            out.append(name.upper())
        else:
            out.append(name.upper() if name.startswith("f") and name[1:].isdigit()
                       else name.capitalize())
    return sep.join(out)


# ---------------------------------------------------------------------------
# Zotero detection
# ---------------------------------------------------------------------------
#
# Zotero is a common companion tool for academic users — almost all of
# our users have it installed.  But it changes how doi.org URLs behave:
# Zotero's macOS app, its "Open in Zotero" feature, and its browser
# connector all intercept doi.org URLs and route the user to the
# CURRENTLY-OPENED PDF in Zotero, NOT the paper they actually selected.
# This makes a perfectly-correct lookup silently do the wrong thing,
# and is the single most common "why does every lookup show the same
# paper?" complaint we see.
#
# We detect Zotero at lookup time and route the user to a search
# engine instead when they're in "auto" mode.  The user can still
# force doi.org via the routing menu ("Always DOI") or by editing
# ``route_mode`` in the config.

def is_zotero_installed() -> bool:
    """Return True if Zotero is installed on this Mac.

    Used by :func:`citation_hop.main.lookup` to decide whether to
    bypass ``doi.org`` URLs in favor of a search engine.  Without this
    bypass, Zotero's browser connector / "Open in Zotero" feature
    intercepts ``doi.org`` URLs and reopens the CURRENTLY-OPENED PDF
    in Zotero, not the paper the user actually selected.

    macOS-only for now.  Windows / Linux installations don't have
    this URL-interception problem (Zotero's browser connector is the
    same on every OS, but its macOS app integrates with the system
    URL handler differently), so we only auto-bypass on macOS.

    Detection is *running-process-first* and is **cached for 60 s**
    so a burst of hotkey presses doesn't hit ``pgrep`` / FS stat
    every time.  The Zotero Connector browser extension only
    intercepts doi.org URLs when **Zotero the desktop app is
    running** (the extension's "Save to Zotero" / "Open in Zotero"
    actions call Zotero's local HTTP API at ``127.0.0.1:23119`` —
    if Zotero isn't running, those calls 404 and the extension
    silently does nothing).  So we trust ``pgrep zotero`` more
    than the .app bundle path (the .app can exist but Zotero
    might not be running; or vice versa, Spotlight indexing
    can make ``os.path.isdir`` flaky for a moment).
    """
    if not IS_DARWIN:
        return False
    return _ZOTERO_CACHE.get()


# Process-level Zotero detection cache
# ---------------------------------------------------------------------------
# Caching the result for 60 s is safe because:
#   - the user has to manually quit/launch Zotero (rare on a writing day)
#   - the bypass behaviour is user-visible (a macOS notification fires),
#     so a 60 s lag is acceptable
#   - hotkey mashing (holding ⌘⇧D) would otherwise do 6-12 pgrep calls
#     per second and slow the click-through to the browser
class _TTLCache:
    """Tiny TTL cache for process detection (no external deps)."""
    __slots__ = ("_value", "_expires_at", "_loader", "_ttl")

    def __init__(self, loader, ttl_seconds: float = 60.0):
        self._loader = loader
        self._ttl = ttl_seconds
        self._value: Optional[bool] = None
        self._expires_at: float = 0.0

    def get(self) -> bool:
        now = time.monotonic()
        if self._value is None or now >= self._expires_at:
            try:
                self._value = bool(self._loader())
            except Exception:
                # Loader failed (e.g. pgrep not on PATH) — fail closed
                # (assume Zotero is NOT installed so we keep using
                # doi.org).  This is the safer default because the
                # worst case is the user gets the old "shows Zotero
                # current PDF" behaviour, not a permanent redirect.
                self._value = False
            self._expires_at = now + self._ttl
        return self._value

    def invalidate(self) -> None:
        self._expires_at = 0.0


def refresh_zotero_cache() -> None:
    """Force a re-check of Zotero's running state on next call.

    Call this from the tray when the user toggles the routing mode
    (auto / doi_always / search_always), so the bypass state
    reflects the *current* Zotero process — not a 60-second-old
    cached value.  The cache will re-populate on the next
    ``is_zotero_installed()`` call.
    """
    _ZOTERO_CACHE.invalidate()


# Standard Zotero.app install paths checked by the ``os.path.isdir``
# fallback in ``_detect_zotero_running``.  Exposed as a module-level
# constant so tests can monkeypatch it without rewriting the function.
_ZOTERO_APP_CANDIDATES: tuple = (
    "/Applications/Zotero.app",
    "~/Applications/Zotero.app",
    "/Applications/Zotero beta.app",
    "~/Applications/Zotero beta.app",
)


def _detect_zotero_running() -> bool:
    """Check whether the Zotero desktop app is currently running.

    Strategy (in order of preference):

    1. ``pgrep -x zotero`` — fast, no FS access, race-free
    2. Fallback: ``os.path.isdir`` on the standard install paths
       (in case ``pgrep`` is missing — unusual on macOS but possible
       in stripped-down sandboxes)

    The candidate paths live in :data:`_ZOTERO_APP_CANDIDATES` at
    module scope so tests can ``monkeypatch.setattr(platform_utils,
    "_ZOTERO_APP_CANDIDATES", ("/tmp/fake/Zotero.app",))`` to point
    at a fixture path.  Without this, the test would have to monkey
    with the FS at the *real* install locations, which is racy and
    platform-dependent.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-x", "zotero"],
            capture_output=True, text=True, timeout=1.0,
        )
        if r.returncode == 0 and r.stdout.strip():
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    # Fallback: at least the .app bundle exists, even if Zotero isn't
    # currently running.  This is a weaker signal but it still hints
    # the user has Zotero installed, so the bypass is a reasonable
    # default.  If they have the .app but never run it, the bypass
    # is still safe (just routes to Scholar instead of doi.org,
    # which works either way).
    candidates = tuple(
        os.path.expanduser(p) for p in _ZOTERO_APP_CANDIDATES
    )
    return any(os.path.isdir(p) for p in candidates)


_ZOTERO_CACHE = _TTLCache(_detect_zotero_running, ttl_seconds=60.0)


def frontmost_app_name() -> str:
    """Return the name of the frontmost application on macOS, or
    ``""`` on other platforms / on error.

    Uses AppleScript ``System Events`` to query the frontmost app.
    Slower than :func:`is_zotero_installed` (subprocess + osascript
    startup, ~150-400 ms on cold start), so call it sparingly — only
    when the result actually changes behaviour.
    """
    if not IS_DARWIN:
        return ""
    script = (
        'tell application "System Events" to return name of '
        '(first application process whose frontmost is true)'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=1.5,
        )
        return (result.stdout or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return ""


def build_scholar_url(text: str) -> str:
    """Build a Google Scholar search URL from *text*.

    Used as the fallback when :func:`is_zotero_installed` is True and
    the user is in ``auto`` mode.  We use Scholar (not the user's
    configured search engines) because:

    1. Scholar is the most universal search engine for academic
       citations, so the user is more likely to find the right paper
       from a (possibly wrong) query string.
    2. The configured search engines may include a doi.org URL
       template somewhere, which would defeat the bypass.
    """
    import urllib.parse
    return "https://scholar.google.com/scholar?q=" + urllib.parse.quote(text or "")


# Path to the local Zotero SQLite database.  Set by :func:`_resolve_zotero_db`
# the first time we need it, then reused.  Read-only access — never modified.
_ZOTERO_DB_PATH: Optional[str] = None


def _resolve_zotero_db() -> Optional[str]:
    """Find the live Zotero database path.

    Priority:
    1. The path declared in Zotero's own preferences
       (``extensions.zotero.dataDir`` / ``useDataDir``).
    2. The default ``~/Zotero/zotero.sqlite``.

    Returns ``None`` if the database is not found.  Always returns the
    path even if Zotero isn't currently running — the caller decides
    whether to attempt access.
    """
    global _ZOTERO_DB_PATH
    if _ZOTERO_DB_PATH:
        return _ZOTERO_DB_PATH
    try:
        prefs_path = os.path.expanduser(
            "~/Library/Application Support/Zotero/Profiles"
        )
        if os.path.isdir(prefs_path):
            for prof in os.listdir(prefs_path):
                prefs_js = os.path.join(prefs_path, prof, "prefs.js")
                if not os.path.isfile(prefs_js):
                    continue
                try:
                    with open(prefs_js, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                m = re.search(
                    r'user_pref\("extensions\.zotero\.dataDir",\s*"([^"]+)"\)',
                    text,
                )
                use_dd = re.search(
                    r'user_pref\("extensions\.zotero\.useDataDir",\s*true\)',
                    text,
                )
                if m and use_dd:
                    candidate = os.path.join(m.group(1), "zotero.sqlite")
                    if os.path.isfile(candidate):
                        _ZOTERO_DB_PATH = candidate
                        return candidate
    except Exception:  # noqa: BLE001
        pass
    fallback = os.path.expanduser("~/Zotero/zotero.sqlite")
    if os.path.isfile(fallback):
        _ZOTERO_DB_PATH = fallback
    return _ZOTERO_DB_PATH


def lookup_zotero_item_by_doi(doi: str) -> Optional[dict]:
    """If a Zotero item with this DOI exists, return its Zotero key.

    Zotero stores its library in a local SQLite database.  If the
    citation the user selected corresponds to an item already in their
    Zotero library, we can return its Zotero deep-link
    (``zotero://select/library/items/<KEY>``) — that opens the right
    PDF reader pane (or the abstract page) directly inside Zotero,
    bypassing the browser entirely and avoiding the
    connector-intercept trap.

    Returns ``None`` if the item isn't found, Zotero's DB is locked,
    or any error occurs.  Errors are silent: this is a best-effort
    enhancement.
    """
    if not doi:
        return None
    db_path = _resolve_zotero_db()
    if not db_path:
        return None
    # Read the DB via a copy to avoid blocking on Zotero's writer lock.
    try:
        import shutil as _sh
        import tempfile
        tmp = tempfile.NamedTemporaryFile(prefix="zh_zdb_", suffix=".sqlite", delete=False)
        tmp.close()
        try:
            _sh.copyfile(db_path, tmp.name)
        except OSError:
            os.unlink(tmp.name)
            return None
        try:
            import sqlite3
            conn = sqlite3.connect(tmp.name)
            try:
                cur = conn.cursor()
                cur.execute(
                    'SELECT fieldID FROM fields WHERE fieldName="DOI"'
                )
                row = cur.fetchone()
                if not row:
                    return None
                doi_fid = row[0]
                cur.execute(
                    "SELECT id.itemID, i.key "
                    "FROM itemData id "
                    "JOIN itemDataValues v ON id.valueID = v.valueID "
                    "JOIN items i ON i.itemID = id.itemID "
                    "WHERE id.fieldID = ? AND v.value = ? "
                    "LIMIT 1",
                    (doi_fid, doi.strip()),
                )
                row = cur.fetchone()
                if not row:
                    return None
                item_id, key = row
                # Pull title for logging
                cur.execute('SELECT fieldID FROM fields WHERE fieldName="title"')
                tr = cur.fetchone()
                title = ""
                if tr:
                    cur.execute(
                        "SELECT v.value FROM itemData id "
                        "JOIN itemDataValues v ON id.valueID = v.valueID "
                        "WHERE id.itemID=? AND id.fieldID=?",
                        (item_id, tr[0]),
                    )
                    rr = cur.fetchone()
                    if rr:
                        title = rr[0]
                return {"itemID": item_id, "key": key, "title": title}
            finally:
                conn.close()
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        return None


def zotero_select_url(key: str) -> str:
    """Return a ``zotero://select/library/items/<KEY>`` URL."""
    return f"zotero://select/library/items/{key}"


def resolve_publisher_url(doi: str, timeout: float = 4.0) -> Optional[str]:
    """Follow the ``doi.org/{doi}`` redirect server-side and return the
    final publisher URL.

    Zotero's browser connector intercepts ``doi.org`` URLs in the
    browser and re-opens the *currently-selected* Zotero item instead
    of letting the browser navigate to the publisher page.  But the
    connector does **not** intercept publisher URLs (e.g.
    ``https://www.tandfonline.com/doi/full/10.1080/...``).  By
    resolving the redirect ourselves (an HTTP HEAD/GET that follows
    301/302s) and handing the publisher URL to ``webbrowser.open``,
    we give the user the actual paper page without Zotero getting in
    the way.

    Returns ``None`` on any failure (network error, timeout, missing
    ``requests``, or the redirect didn't actually move us off
    ``doi.org``).  Callers should fall back to
    :func:`build_scholar_url` when this happens.

    Note: we don't require a 2xx status code — many publishers return
    403 to bot-like HTTP requests but load fine in a real browser.
    The only thing we care about is: did the redirect chain move us
    off ``doi.org``?  If yes, the publisher URL is usable.
    """
    if not doi:
        return None
    try:
        import requests  # type: ignore
    except ImportError:
        LOG.debug("requests not available; cannot resolve publisher URL")
        return None

    # Domains that are *not* real publisher pages — they're DOI registry
    # intermediaries / chooser pages.  Landing here means the redirect
    # didn't reach a usable destination, so we return None and let the
    # caller fall back to Scholar search.
    _REJECT_HOSTS = (
        "chooser.crossref.org",
        "data.crossref.org",
        "api.crossref.org",
        "crossref.org",
        "doi.org",
        "dx.doi.org",
        "doi.crossref.org",
    )

    def _is_usable(final_url: str) -> bool:
        """True if *final_url* moved past doi.org AND isn't a known
        intermediary landing page."""
        if not final_url:
            return False
        from urllib.parse import urlparse
        host = (urlparse(final_url).hostname or "").lower()
        if not host:
            return False
        # Reject any host that ends with a known intermediary domain
        # (covers both bare ``crossref.org`` and ``www.crossref.org``).
        for bad in _REJECT_HOSTS:
            if host == bad or host.endswith("." + bad):
                return False
        return True

    url = f"https://doi.org/{doi}"
    headers = {"User-Agent": "citationHop/1.2 (+mailto:syz@shiyangzheng.top)"}
    try:
        # HEAD first — cheaper, most DOI redirects support it.
        resp = requests.head(url, allow_redirects=True, timeout=timeout,
                             headers=headers)
        final = resp.url or ""
        if _is_usable(final):
            return final
        # Some publishers reject HEAD; fall back to GET with stream
        # and close immediately to avoid downloading the full page.
        resp = requests.get(url, allow_redirects=True, timeout=timeout,
                            stream=True, headers=headers)
        final = resp.url or ""
        resp.close()
        if _is_usable(final):
            return final
    except Exception as e:  # noqa: BLE001
        LOG.debug("resolve_publisher_url failed for %s: %s", doi, e)
    return None


def clean_zotero_noise(text: str) -> str:
    """Strip Zotero PDF reader annotation noise from *text*.

    When you copy text from Zotero's built-in PDF reader, the clipboard
    often includes annotation markers that pollute citation searches::

        Heidari, K. ... 161–83. 2 📊. https://doi.org/10.1080/...

    The ``2 📊. https://doi.org/...`` suffix is Zotero's in-text
    citation annotation — it's useful inside Zotero but destroys search
    engine queries.  This function strips:

    * Annotation markers: ``\\d+ <emoji>. <url>``
    * Bare DOI URLs: ``https://doi.org/...``, ``https://dx.doi.org/...``
    * Trailing whitespace / newlines left behind
    """
    if not text:
        return text
    import re
    # Strip Zotero annotation markers: "2 📊. https://doi.org/..."
    # Emoji code points above U+FFFF need \U (8 hex digits), not \u (4).
    text = re.sub(
        r"\s*\d+\s+[\u2600-\u27bf\u2190-\u21ff\u2b00-\u2bff\U0001f300-\U0001f9ff\U0001fa70-\U0001faff]+\.?\s*https?://\S+",
        "",
        text,
    )
    # Also strip "2 📊." without a URL (Zotero sometimes appends just the marker)
    text = re.sub(
        r"\s*\d+\s+[\u2600-\u27bf\u2190-\u21ff\u2b00-\u2bff\U0001f300-\U0001f9ff\U0001fa70-\U0001faff]+\.?\s*$",
        "",
        text,
    )
    # Strip any remaining bare DOI URLs
    text = re.sub(
        r"\s*https?://(?:dx\.)?doi\.org/\S+",
        "",
        text,
    )
    # Clean up trailing whitespace/newlines
    return text.strip()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

__all__ = [
    "IS_DARWIN",
    "IS_WIN",
    "IS_LINUX",
    "SYSTEM",
    "confirm",
    "notify",
    "simulate_copy",
    "get_selection_via_copy",
    "copy_to_clipboard",
    "open_path",
    "load_tray_icon",
    "keystroke_label",
    "get_package_dir",
    "get_assets_dir",
    "is_zotero_installed",
    "frontmost_app_name",
    "build_scholar_url",
    "resolve_publisher_url",
    "clean_zotero_noise",
    "lookup_zotero_item_by_doi",
    "zotero_select_url",
]
