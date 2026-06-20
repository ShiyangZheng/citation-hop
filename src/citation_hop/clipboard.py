"""Safe clipboard access for the hotkey flow.

The hotkey needs to capture the user's current text selection. We do
this by:

    1. snapshotting the current clipboard contents
    2. writing a unique sentinel string to the clipboard
    3. simulating Cmd+C (macOS) or Ctrl+C (Windows / Linux)
    4. waiting briefly for the target app to put the new selection in
    5. reading the clipboard; if the sentinel is still present, Cmd+C
       didn't fire and we return "" instead of the stale content
    6. applying defensive filters (length cap, terminal/log heuristics)
    7. restoring the original contents

This keeps the user's clipboard clean — we never leave our lookup
result there unless we explicitly want to (we DO copy the resolved
DOI back once the lookup succeeds; that's the documented user-visible
side effect).

Why the sentinel matters
------------------------
The previous version cleared the clipboard (``pyperclip.copy("")``)
then sent Cmd+C, and treated "non-empty after Cmd+C" as "user has a
selection".  That works in the happy path, but **fails silently** when
Cmd+C doesn't reach the foreground app — e.g. when the user clicked
into the menu bar, when Accessibility permission was revoked, or when
the previous lookup left a long string (a full PDF paragraph, the
contents of a ``cat /tmp/...log``, etc.) on the pasteboard.  In those
cases the "selection capture" was actually the *stale* clipboard, and
the lookup pipeline would dutifully resolve whichever substring of
that log happened to look like a citation.

The sentinel gives us an unambiguous signal: if it's still on the
pasteboard after Cmd+C, nothing was copied.  We return "" and the
hotkey surfaces the existing "Nothing selected" notification instead
of opening the wrong paper.

Defensive filters
-----------------
Even when Cmd+C *does* fire, the captured text might not be a
citation:

* **Length cap** — full bibliographic entries are < 1500 characters
  in practice.  Longer selections are almost certainly a log dump,
  a Terminal scrollback, or a PDF chunk with surrounding context.
* **Terminal / log heuristics** — content with a high density of
  JSON markers (``{``, ``}``, ``"``), shell prompts (``$ ``,
  ``% ``, ``❯``, ``>>>``), or trailing newlines is not a citation.

When any of these fire, we return ``""`` and the hotkey surfaces the
"Doesn't look like a citation" notification (same code path as the
heuristic gate in :mod:`detector`).

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
import re
import time
import uuid

import pyperclip

from .platform_utils import (
    IS_DARWIN,
    simulate_copy,
    _simulate_copy_applescript,  # used here as a recovery path on macOS
)

LOG = logging.getLogger(__name__)

_COPY_DELAY_S = 0.30  # increased 2026-06-20: Zotero's activate+copy round-trip
                      # takes ~250 ms on Apple Silicon; the previous 0.12 s
                      # was too short and we read the clipboard before Zotero
                      # had finished putting the new selection on it.

# A full bibliographic entry is at most a few hundred characters; APA
# reference lists are typically < 500.  Anything past 1500 chars is
# almost certainly not a single citation — it's a log dump, terminal
# scrollback, or a PDF chunk with surrounding context.  We discard it.
_MAX_CITATION_LEN = 1500

# Number of newlines that triggers the "this is multi-paragraph text,
# not a single citation" heuristic.  Real citations may contain 1–2
# newlines (PDF copy-paste often inserts line breaks inside the title
# field), but 5+ is a strong signal of structured/log content.
_MAX_NEWLINES = 5

# Sentinel marker written to the clipboard right before sending Cmd+C.
# If Cmd+C doesn't replace the pasteboard, the sentinel survives and
# we know the keystroke didn't reach any app with a selection.
_SENTINEL_PREFIX = "__citation_hop_sentinel_"

# Heuristics for "this looks like terminal / log / shell output, not
# a citation".  We use a *conservative* set so legitimate citations
# that happen to contain parentheses / braces don't get filtered.
_TERMINAL_PROMPT_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:\$ |% |>|>|\$ |\.\/|[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+[^a-zA-Z]"
    r"|\$\(\s*|>>>|\.\.\.\s*$|❯|➜|⏎)"
)
_JSON_OBJECT_RE = re.compile(r'\{[^{}]*"[a-zA-Z_]+"\s*:\s*')
_LOG_LINE_RE = re.compile(
    r"(?:^|\n)\s*\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}"
)
_SHELL_CMD_RE = re.compile(
    r"(?:^|\n)\s*(?:cat|ls|cd|grep|find|echo|python|python3|pip|brew|"
    r"git|npm|node|osascript|sudo|source|export|alias|"
    r"\.venv/bin/python|citation_hop)\b"
)


def _safe_paste() -> str:
    try:
        return pyperclip.paste() or ""
    except Exception:  # pragma: no cover
        return ""


def _safe_copy(text: str) -> None:
    try:
        pyperclip.copy(text)
    except Exception:  # pragma: no cover
        LOG.debug("pyperclip.copy failed")


def _make_sentinel() -> str:
    """Return a unique sentinel unlikely to collide with anything
    the user might have on their clipboard."""
    return _SENTINEL_PREFIX + uuid.uuid4().hex + "__"


def _looks_like_terminal_output(text: str) -> bool:
    """Conservative check: is this clearly terminal / log output?

    We intentionally require *multiple* signals to fire so a single
    suspicious feature (e.g. one ``$`` in a citation title) doesn't
    cause a false positive.  Currently triggers on:

    * terminal prompt at line start (``$ ``, ``% ``, ``>>>``, etc.)
    * JSON object structure (``{"key":``)
    * timestamped log line (``2025-06-19T12:34`` / ``2025-06-19 12:34``)
    * shell command at line start (``cat``, ``python3``, etc.)

    Any one of these is enough to bail — real citations essentially
    never start with ``$ cat`` or ``{`` at the beginning of a line.
    """
    if not text:
        return False
    return bool(
        _TERMINAL_PROMPT_RE.search(text)
        or _JSON_OBJECT_RE.search(text)
        or _LOG_LINE_RE.search(text)
        or _SHELL_CMD_RE.search(text)
    )


def get_selection() -> str:
    """Return the currently selected text, leaving the clipboard clean.

    Robust against silent Cmd+C failures: if the synthetic Cmd+C didn't
    actually replace a sentinel we wrote to the clipboard first, we
    return ``""`` instead of the stale pasteboard contents.

    Returns ``""`` (not ``None``) on every failure path so callers can
    pass the result straight to ``lookup()`` without a None check.
    """
    saved = _safe_paste()
    sentinel = _make_sentinel()

    try:
        # Stage 1: write a unique sentinel to the clipboard so we can
        # later verify whether Cmd+C actually replaced it.
        _safe_copy(sentinel)

        simulate_copy()
        time.sleep(_COPY_DELAY_S)
        selected = _safe_paste()

        # macOS retry: some sandboxed apps (Electron-based ones, in
        # particular) don't always update the pasteboard on the first
        # synthetic Cmd+C.  One more attempt with the same path is
        # enough in practice.
        if sentinel in selected and IS_DARWIN:
            _simulate_copy_applescript()
            time.sleep(_COPY_DELAY_S)
            selected = _safe_paste()

        # Stage 2: verify Cmd+C actually fired.  If the sentinel is
        # still on the pasteboard, no foreground app had a selection
        # (or the keystroke didn't reach one).  Don't process the
        # stale content — return empty.
        if sentinel in selected:
            LOG.debug("Cmd+C didn't replace sentinel — no selection captured")
            return ""

        # Stage 3: defensive filters.  Even when Cmd+C succeeds, the
        # captured text might not be a citation (log dump, terminal
        # output, multi-paragraph PDF chunk).  These cases used to
        # produce "wrong paper" bugs because the heuristic gate in
        # detector.py saw a citation-looking substring inside a giant
        # log blob and matched against the wrong paper.
        if len(selected) > _MAX_CITATION_LEN:
            LOG.warning(
                "Captured selection is %d chars (max %d) — likely a log "
                "dump or terminal scrollback, not a citation. Discarding.",
                len(selected), _MAX_CITATION_LEN,
            )
            return ""

        if selected.count("\n") > _MAX_NEWLINES:
            LOG.warning(
                "Captured selection has %d newlines (max %d) — looks like "
                "multi-paragraph content, not a single citation. Discarding.",
                selected.count("\n"), _MAX_NEWLINES,
            )
            return ""

        if _looks_like_terminal_output(selected):
            LOG.warning(
                "Captured selection looks like terminal / log output, "
                "not a citation. Discarding. First 80 chars: %r",
                selected[:80],
            )
            return ""

        return selected
    finally:
        # Restore the user's original clipboard contents regardless of
        # what happened above.  If the lookup later wants to copy the
        # resolved DOI back, that copy happens AFTER this finally
        # block, so it doesn't get clobbered.
        _safe_copy(saved)


def copy_to_clipboard(text: str) -> None:
    """Public helper: put *text* on the clipboard."""
    pyperclip.copy(text)


__all__ = ["get_selection", "copy_to_clipboard"]