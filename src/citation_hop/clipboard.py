"""Safe clipboard access for the hotkey flow.

The hotkey needs to capture the user's current text selection. We do
this by:

    1. snapshotting the current clipboard contents
    2. simulating Cmd+C (macOS) or Ctrl+C (Windows / Linux)
    3. waiting briefly for the target app to put the new selection in
    4. reading the clipboard
    5. restoring the original contents

This keeps the user's clipboard clean — we never leave our lookup
result there unless we explicitly want to (we DO copy the resolved
DOI back once the lookup succeeds; that's the documented user-visible
side effect).

The macOS-specific AppleScript fallback for sandboxed apps lives
here too — it's only relevant on macOS, and only used when the
synthetic ``Cmd+C`` produced an empty clipboard.
"""

from __future__ import annotations

import logging
import subprocess
import time

import pyperclip

from .platform_utils import (
    IS_DARWIN,
    simulate_copy,
)

LOG = logging.getLogger(__name__)

_COPY_DELAY_S = 0.12


def _simulate_copy_applescript() -> None:
    """Last-ditch fallback for apps that ignore synthetic Cmd+C events
    on macOS.  Requires Automation → System Events permission."""
    if not IS_DARWIN:
        return
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
    simply returns whatever ends up on the clipboard after the copy.
    """
    try:
        original = pyperclip.paste()
    except Exception:  # pragma: no cover
        original = ""

    try:
        try:
            pyperclip.copy("")  # clear so we can detect "nothing was selected"
        except Exception:  # pragma: no cover
            pass

        simulate_copy()
        time.sleep(_COPY_DELAY_S)
        selected = ""
        try:
            selected = pyperclip.paste() or ""
        except Exception:  # pragma: no cover
            selected = ""

        # macOS-only AppleScript fallback for sandboxed apps.
        if not selected and IS_DARWIN:
            _simulate_copy_applescript()
            time.sleep(_COPY_DELAY_S)
            try:
                selected = pyperclip.paste() or ""
            except Exception:  # pragma: no cover
                selected = ""

        return selected
    finally:
        try:
            pyperclip.copy(original)
        except Exception:  # pragma: no cover
            LOG.debug("Failed to restore original clipboard contents")


def copy_to_clipboard(text: str) -> None:
    """Public helper: put *text* on the clipboard."""
    pyperclip.copy(text)


__all__ = ["get_selection", "copy_to_clipboard"]
