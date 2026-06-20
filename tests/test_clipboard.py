"""Tests for clipboard capture — specifically the sentinel-based
detection of silent Cmd+C failures and the terminal/log filters
that prevent "stale clipboard resolves to wrong paper" bugs.

These tests are pure-Python: they monkey-patch :mod:`pyperclip` and
:mod:`citation_hop.clipboard`'s ``simulate_copy`` shim, so they
don't need Accessibility / Automation permissions or a real macOS
session.  CI can run them on Linux.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# Make the src/ layout importable without an editable install.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from citation_hop import clipboard  # noqa: E402
from citation_hop.clipboard import (  # noqa: E402
    _looks_like_terminal_output,
    get_selection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_clipboard():
    """Replace pyperclip.copy / pyperclip.paste with a shared list.

    ``clipboard`` is the in-process pasteboard; tests can read / write
    it directly.  ``copy_count`` records how many ``pyperclip.copy``
    calls happened (useful for verifying the sentinel/restore dance).
    """
    state = {"clipboard": "ORIGINAL", "copy_count": 0}

    def _copy(text):
        state["clipboard"] = text
        state["copy_count"] += 1

    def _paste():
        return state["clipboard"]

    with mock.patch.object(clipboard.pyperclip, "copy", side_effect=_copy), \
         mock.patch.object(clipboard.pyperclip, "paste", side_effect=_paste):
        yield state


# ---------------------------------------------------------------------------
# _looks_like_terminal_output
# ---------------------------------------------------------------------------


class TestLooksLikeTerminalOutput:
    @pytest.mark.parametrize("text", [
        # Shell prompts at line start
        "admin@mbp ~ % cat /tmp/foo.log\n",
        "$ ls -la\ntotal 12\n",
        ">>> print('hello')\n",
        "❯ pwd\n",
        # JSON object structure
        '{"ts": 1234, "stage": "result", "doi": "10.1/x"}\n',
        # Timestamped log line
        "2025-06-19T12:34:56 INFO starting\n",
        "2025-06-19 12:34:56 ERROR something broke\n",
        # Shell commands at line start
        "python3 -m citation_hop\n",
        "git status\n",
        "cat /tmp/citation_hop.log\n",
    ])
    def test_detects_terminal_or_log(self, text):
        assert _looks_like_terminal_output(text), f"Should flag: {text!r}"

    @pytest.mark.parametrize("text", [
        # Real APA citations — none should trip the detector
        "Heidari, Kamal, and Mahnaz Aliyar. ‘Thirty-Five Years of Research "
        "on Idioms in Second Language Acquisition: A Methodological Review’. "
        "Research Synthesis in Applied Linguistics 1, no. 1 (2025): 161–83.",
        "Wray, A. (2002). Formulaic Language and the Lexicon. "
        "Cambridge University Press.",
        "Sinclair, J. (1991). Corpus, Concordance, Collocation. "
        "Oxford University Press.",
        # A reference that happens to mention $ in the title (rare but valid)
        "Smith, J. (2020). The $100 laptop. Journal of Things, 1(1), 1-2.",
        # Plain in-text citation
        "(Wray, 2002)",
        "Wray and Perkins (2000)",
    ])
    def test_does_not_flag_real_citations(self, text):
        assert not _looks_like_terminal_output(text), f"False positive: {text!r}"


# ---------------------------------------------------------------------------
# get_selection: happy path
# ---------------------------------------------------------------------------


class TestGetSelectionHappyPath:
    def test_returns_user_selection(self, fake_clipboard):
        """Cmd+C replaces sentinel with user's selection → return it."""

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = (
                "Wray, A. (2002). Formulaic Language and the Lexicon. "
                "Cambridge University Press."
            )

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert "Wray" in result
        assert "2002" in result

    def test_restores_original_clipboard(self, fake_clipboard):
        """After get_selection returns, the clipboard should hold the
        user's *original* content (not the sentinel, not the selection)."""

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = "Wray, A. (2002). ..."

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            get_selection()

        assert fake_clipboard["clipboard"] == "ORIGINAL"


# ---------------------------------------------------------------------------
# get_selection: silent Cmd+C failure
# ---------------------------------------------------------------------------


class TestGetSelectionSilentFailure:
    def test_returns_empty_when_cmd_c_does_not_replace_sentinel(self, fake_clipboard):
        """The bug we're fixing: Cmd+C doesn't fire, sentinel survives,
        get_selection returns '' instead of the stale clipboard."""

        # simulate_copy is a no-op — the sentinel survives.
        def fake_simulate_copy():
            pass

        # macOS retry is also a no-op.
        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "_simulate_copy_applescript",
                               side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""
        # And the original clipboard is preserved.
        assert fake_clipboard["clipboard"] == "ORIGINAL"

    def test_real_bug_scenario_terminal_log(self, fake_clipboard):
        """Reproduces the actual user-reported bug.

        The user had ``cat /tmp/citation_hop.log`` output on the
        clipboard.  They pressed the hotkey in Terminal with no
        selection.  Cmd+C didn't fire.  The old code would have read
        the stale 3,581-char log; the new code returns ''.
        """
        huge_log = (
            '{"ts": 1781955351.29, "text_preview": "Heidari, Kamal, and Mahnaz Aliyar."}\n'
            '{"ts": 1781955365.31, "text_preview": "Heidari, Kamal, and Mahnaz Aliyar."}\n'
        ) * 50  # 3,500+ chars

        # The "saved" clipboard is the huge log — exactly the user scenario.
        fake_clipboard["clipboard"] = huge_log

        # Cmd+C is a no-op.
        def fake_simulate_copy():
            pass

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "_simulate_copy_applescript",
                               side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""
        # The stale log is restored, not left on the clipboard.
        assert fake_clipboard["clipboard"] == huge_log


# ---------------------------------------------------------------------------
# get_selection: defensive filters
# ---------------------------------------------------------------------------


class TestGetSelectionDefensiveFilters:
    def test_rejects_oversized_selection(self, fake_clipboard):
        """A 2000-char selection is almost certainly not a citation."""

        long_text = "Heidari, K. " + "x" * 2000  # ~2010 chars

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = long_text

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""

    def test_rejects_multi_paragraph_content(self, fake_clipboard):
        """A selection with 6+ newlines is multi-paragraph, not a citation."""

        multiline = "First paragraph line 1\nSecond paragraph line 2\n" * 4

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = multiline

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""

    def test_rejects_terminal_output(self, fake_clipboard):
        """A 'cat' command line is not a citation."""

        terminal = "admin@mbp ~ % cat /tmp/citation_hop.log\n"

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = terminal

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""

    def test_rejects_json_log_line(self, fake_clipboard):
        """A JSON line with timestamps is not a citation."""

        log_line = '{"ts": 1781955351.29, "text_preview": "Heidari, Kamal"}\n'

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = log_line

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == ""


# ---------------------------------------------------------------------------
# In-text citation capture (regression — was already in v1.2, verify still works)
# ---------------------------------------------------------------------------


class TestInTextCitationStillWorks:
    def test_short_in_text_citation_passes_through(self, fake_clipboard):
        """In-text citations are short (well under 1500 chars) and have
        no terminal markers — they must still be captured."""

        in_text = "(Wray, 2002)"

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = in_text

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == "(Wray, 2002)"

    def test_narrative_in_text_citation(self, fake_clipboard):
        in_text = "Sinclair (1991)"

        def fake_simulate_copy():
            fake_clipboard["clipboard"] = in_text

        with mock.patch.object(clipboard, "simulate_copy", side_effect=fake_simulate_copy), \
             mock.patch.object(clipboard, "IS_DARWIN", True):
            result = get_selection()

        assert result == "Sinclair (1991)"