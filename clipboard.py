"""Safe clipboard access for the hotkey flow.

The hotkey needs to capture the user's current text selection. We do this
by:

    1. snapshotting the current clipboard contents
    2. simulating Cmd+C
    3. waiting briefly for the target app to put the new selection in
    4. reading the clipboard
    5. restoring the original contents

This keeps the user's clipboard clean — we never leave our lookup result
there unless we explicitly want to (we DO copy the resolved DOI back
once the lookup succeeds; that's the documented user-visible side effect).
"""

from __future__ import annotations

import logging
import platform
import subprocess
import time
from typing import Optional

import pyperclip

# pynput is macOS-specific in this module; on other platforms we
# fall back to whatever pyperclip can give us.
if platform.system() == "Darwin":
    try:
        from pynput.keyboard import Controller, Key  # type: ignore
    except Exception:  # pragma: no cover - import only fails on non-mac
        Controller = None
        Key = None
else:
    Controller = None
    Key = None

LOG = logging.getLogger(__name__)

_COPY_DELAY_S = 0.12


def _simulate_copy_pynput() -> None:
    if Controller is None or Key is None:
        return
    kb = Controller()
    kb.press(Key.cmd)
    kb.press("c")
    kb.release("c")
    kb.release(Key.cmd)


def _simulate_copy_applescript() -> None:
    """Last-ditch fallback for apps that ignore synthetic Cmd+C events.

    Requires Automation → System Events permission on macOS.
    """
    script = (
        'tell application "System Events" to keystroke "c" using command down'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        LOG.debug("AppleScript fallback failed: %s", e)


def get_selection() -> str:
    """Return the currently selected text, leaving the clipboard clean.

    On non-macOS platforms (or when permissions are missing), this
    simply returns whatever is on the clipboard.
    """
    original = pyperclip.paste()
    try:
        pyperclip.copy("")  # clear so we can detect "nothing was selected"
        _simulate_copy_pynput()
        time.sleep(_COPY_DELAY_S)
        selected = pyperclip.paste()

        # If pynput didn't deliver (some sandboxed apps), try AppleScript.
        if not selected:
            _simulate_copy_applescript()
            time.sleep(_COPY_DELAY_S)
            selected = pyperclip.paste()

        return selected
    finally:
        # Always restore what was on the clipboard before we started.
        try:
            pyperclip.copy(original)
        except Exception:  # pragma: no cover - clipboard can fail
            LOG.debug("Failed to restore original clipboard contents")


def copy_to_clipboard(text: str) -> None:
    """Public helper: put *text* on the clipboard."""
    pyperclip.copy(text)


__all__ = ["get_selection", "copy_to_clipboard"]
