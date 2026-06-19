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

def notify(title: str, message: str, *, subtitle: Optional[str] = None) -> None:
    """Show a non-blocking desktop notification.  Never raises."""
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
    loaded.  We deliberately do *not* do an AppleScript fallback
    outside of macOS — synthetic Ctrl+C is much more reliable on
    Windows / Linux than its AppleScript equivalent.
    """
    try:
        from pynput.keyboard import Controller, Key  # type: ignore
    except Exception:  # pragma: no cover
        return

    modifier = Key.cmd if IS_DARWIN else Key.ctrl
    kb = Controller()
    kb.press(modifier)
    kb.press("c")
    kb.release("c")
    kb.release(modifier)


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
]
