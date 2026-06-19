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

Important macOS note
--------------------
``simulate_copy`` (in :mod:`platform_utils`) uses **AppleScript on
macOS** rather than pynput's ``Controller``.  Pynput's Controller
posts a synthetic CGEvent from inside the listener's own CFRunLoop,
which on macOS 15 / Apple Silicon re-enters the HID event tap and
crashes the process with ``SIGILL`` (the dreaded ``zsh: illegal
hardware instruction``).  AppleScript's ``keystroke`` is delivered
through System Events → WindowServer and does not re-enter.
"""

from __future__ import annotations

import logging
import time

import pyperclip

from .platform_utils import (
    IS_DARWIN,
    simulate_copy,
    _simulate_copy_applescript,  # used here as a recovery path on macOS
)

LOG = logging.getLogger(__name__)

_COPY_DELAY_S = 0.12


def get_selection() -> str:
    """Return the currently selected text, leaving the clipboard clean.

    On non-macOS platforms (or when permissions are missing), this
    simply returns whatever ends up on the clipboard after the copy.

    On macOS the primary copy path is :func:`platform_utils.simulate_copy`
    (which now uses AppleScript).  If the first attempt produced an
    empty clipboard — which can happen for apps that don't respond to
    System Events' keystroke within ``_COPY_DELAY_S`` — we retry once
    with a slightly longer wait before giving up.
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

        # macOS retry: some sandboxed apps (e.g. Electron-based ones)
        # don't always update the pasteboard on the first synthetic
        # Cmd+C.  One more attempt with the same path is enough in
        # practice; if it still fails we just return the empty string
        # and the hotkey handler surfaces a friendly "Nothing selected"
        # notification.
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
