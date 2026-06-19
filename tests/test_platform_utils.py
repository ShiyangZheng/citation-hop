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
    for name in ("confirm", "notify", "keystroke_label", "simulate_copy"):
        assert hasattr(platform_utils, name), f"missing {name}"
        assert callable(getattr(platform_utils, name))


# ---------------------------------------------------------------------------
# simulate_copy
# ---------------------------------------------------------------------------
#
# v1.1.1: simulate_copy on macOS was switched from pynput Controller to
# AppleScript (``osascript -e 'tell application "System Events" to
# keystroke "c" using command down'``).  The reason: pynput's
# Controller posts a synthetic CGEvent via CGEventPost, and if called
# from inside pynput's listener CFRunLoop, macOS 15 / Apple Silicon
# re-enters the HID event tap and the process dies with SIGILL.
#
# These tests pin the dispatch behaviour so a future refactor can't
# silently reintroduce the crash.

def test_simulate_copy_dispatches_to_applescript_on_macos(monkeypatch):
    """On macOS, simulate_copy must call AppleScript, never pynput
    Controller.  Mock subprocess.run and assert it was invoked with
    the expected osascript payload."""
    if not platform_utils.IS_DARWIN:
        pytest.skip("macOS-only behaviour")

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        # Return a fake CompletedProcess; simulate_copy only inspects
        # returncode / doesn't care about stdout.
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run)

    # Guard: if anyone reintroduces pynput Controller, this will fire.
    controller_called = []

    class _FakeController:
        def __init__(self):
            controller_called.append("ctor")
        def press(self, *_a, **_kw):
            controller_called.append("press")
        def release(self, *_a, **_kw):
            controller_called.append("release")

    import pynput.keyboard as _kb
    monkeypatch.setattr(_kb, "Controller", _FakeController)

    platform_utils.simulate_copy()

    assert controller_called == [], (
        "pynput Controller must NOT be used on macOS — it re-enters "
        "the HID event tap and crashes with SIGILL."
    )
    assert any("osascript" in c[0] for c in calls), (
        f"expected an osascript invocation; got {calls!r}"
    )
    apple = next(c for c in calls if "osascript" in c[0])
    script = apple[-1]  # the osascript -e <script> payload
    assert 'keystroke "c"' in script
    assert "command down" in script


def test_simulate_copy_dispatches_to_pynput_controller_on_win_linux(monkeypatch):
    """On Windows / Linux, simulate_copy should still use pynput
    Controller (SendInput / XTestFakeKeyEvent — no re-entrancy)."""
    if platform_utils.IS_DARWIN:
        pytest.skip("non-macOS behaviour")

    class _FakeController:
        def __init__(self):
            self.calls = []
        def press(self, k, *a, **kw):
            self.calls.append(("press", k))
        def release(self, k, *a, **kw):
            self.calls.append(("release", k))

    fc = _FakeController()
    import pynput.keyboard as _kb
    monkeypatch.setattr(_kb, "Controller", lambda: fc)

    platform_utils.simulate_copy()

    # The four expected calls: press ctrl, press c, release c, release ctrl.
    ops = [c[0] for c in fc.calls]
    assert ops == ["press", "press", "release", "release"]
    # Modifier is Key.ctrl on non-macOS.
    from pynput.keyboard import Key
    assert fc.calls[0][1] == Key.ctrl
    assert fc.calls[1][1] == "c"


def test_simulate_copy_handles_missing_pynput_gracefully(monkeypatch):
    """If pynput.keyboard import blows up on a non-macOS host (rare,
    but possible on stripped CI images), simulate_copy should be a
    silent no-op, not raise."""
    if platform_utils.IS_DARWIN:
        pytest.skip("non-macOS behaviour")

    import pynput.keyboard as _kb

    def boom():
        raise ImportError("pynput not available")

    monkeypatch.setattr(_kb, "Controller", boom)
    # Should not raise.
    platform_utils.simulate_copy()


def test_simulate_copy_handles_missing_osascript_gracefully(monkeypatch):
    """If osascript is missing on a macOS host (it never is in
    practice, but a future container could ship without it),
    simulate_copy should be a silent no-op."""
    if not platform_utils.IS_DARWIN:
        pytest.skip("macOS-only behaviour")

    def fake_run(*_a, **_kw):
        raise FileNotFoundError("osascript missing")

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run)
    # Should not raise.
    platform_utils.simulate_copy()
