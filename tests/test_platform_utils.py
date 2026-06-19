"""Unit tests for the cross-platform UI primitives in platform_utils.

These tests focus on the pure-Python pieces (no dialog pop, no
clipboard, no notification) so they can run on every CI platform
without a display.
"""
from __future__ import annotations

import pytest

from citation_hop import platform_utils
from citation_hop.platform_utils import keystroke_label


# ---------------------------------------------------------------------------
# keystroke_label
# ---------------------------------------------------------------------------
#
# The expected output depends on the host OS, so we use a small fixture
# that snapshots the current behaviour and a parametrized matrix of
# (input, expected) pairs for each platform.
#
# Concretely:
#   - macOS renders modifiers as glyphs (⌘ ⌃ ⌥ ⇧) with no separator.
#   - Windows / Linux render modifiers as words joined with "+".
#   - Function keys, named keys, and single characters have a shared
#     prettification rule across platforms.

_MAC_CASES = [
    ("<cmd>+d",                              "⌘D"),
    ("<cmd>+<shift>+d",                      "⌘⇧D"),
    ("<ctrl>+<alt>+<f5>",                    "⌃⌥F5"),
    ("<cmd>+<shift>+<cmd_l>+l",              "⌘⇧⌘L"),
    ("<alt_l>+<shift_r>+<space>",            "⌥⇧Space"),
    ("<caps_lock>",                          "CapsLock"),
    ("<page_up>",                            "PageUp"),
    ("<up>",                                 "↑"),
    ("<cmd>+<right>",                        "⌘→"),
]

_WIN_CASES = [
    # pynput's <cmd> is the macOS Command key, but on Windows it maps
    # to the actual Windows / Super key.  We render the binding
    # literally (so users see what pynput will *actually* do), not
    # after any friendly "translate" step.  If the user wanted Ctrl+D
    # they should write <ctrl>+d.
    ("<cmd>+d",                              "Win+D"),
    ("<cmd>+<shift>+d",                      "Win+Shift+D"),
    ("<ctrl>+<alt>+<f5>",                    "Ctrl+Alt+F5"),
    ("<alt_l>+<shift_r>+<space>",            "Alt+Shift+Space"),
    ("<caps_lock>",                          "CapsLock"),
    ("<page_down>",                          "PageDown"),
    ("<up>",                                 "↑"),
    ("<cmd>+<right>",                        "Win+→"),
]

_LINUX_CASES = [
    # pynput's <cmd> on Linux maps to the Super / Win key (same as
    # Windows).  We use the "Win" word label to keep Windows / Linux
    # display consistent.
    ("<cmd>+d",                              "Win+D"),
    ("<ctrl>+<alt>+<f5>",                    "Ctrl+Alt+F5"),
    ("<page_up>",                            "PageUp"),
]


@pytest.mark.skipif(not platform_utils.IS_DARWIN, reason="macOS-specific render")
@pytest.mark.parametrize("combo,expected", _MAC_CASES)
def test_keystroke_label_macos(combo, expected):
    assert keystroke_label(combo) == expected


@pytest.mark.skipif(not platform_utils.IS_WIN, reason="Windows-specific render")
@pytest.mark.parametrize("combo,expected", _WIN_CASES)
def test_keystroke_label_windows(combo, expected):
    assert keystroke_label(combo) == expected


@pytest.mark.skipif(not platform_utils.IS_LINUX, reason="Linux-specific render")
@pytest.mark.parametrize("combo,expected", _LINUX_CASES)
def test_keystroke_label_linux(combo, expected):
    assert keystroke_label(combo) == expected


# ---------------------------------------------------------------------------
# Platform-agnostic (input shape) cases
# ---------------------------------------------------------------------------

def test_keystroke_label_empty():
    assert keystroke_label("") == ""
    assert keystroke_label(None) == ""  # type: ignore[arg-type]


def test_keystroke_label_only_separators():
    assert keystroke_label("+") == ""
    assert keystroke_label("+++") == ""


def test_keystroke_label_unknown_named_key_falls_back_to_capitalised():
    # Not a known modifier or pretty key, but a reasonable name.
    out = keystroke_label("<insert>")
    # The fallback rule capitalises the first letter.  Platform-agnostic.
    assert out == "Insert"


def test_keystroke_label_function_keys_capitalised():
    out = keystroke_label("<f12>")
    assert out == "F12"


def test_keystroke_label_no_brackets_treated_as_raw_key():
    # pynput's HotKey.parse does accept "d" without <>, the formatter
    # should treat that the same way.
    out = keystroke_label("d")
    assert out.upper() == "D"


# ---------------------------------------------------------------------------
# Surface checks
# ---------------------------------------------------------------------------

def test_required_exports_present():
    """Every function the smoke test looks up must be importable."""
    for name in ("confirm", "notify", "keystroke_label"):
        assert hasattr(platform_utils, name), f"missing {name}"
        assert callable(getattr(platform_utils, name))
