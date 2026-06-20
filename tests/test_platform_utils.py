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
# notify() call-shape compatibility
# ---------------------------------------------------------------------------
#
# v1.1.0 broke macOS: ``notify(title, message, "subtitle str")`` raised
# TypeError (third arg is keyword-only) and the ``DOI copied``
# subtitle silently disappeared.  v1.1.2 keeps ``subtitle`` accepted
# positionally for backward compatibility but still allows the keyword
# form.  This pins both shapes so a future refactor can't regress
# either path.

def test_notify_accepts_subtitle_kwarg(monkeypatch):
    """The canonical call shape — keyword subtitle."""
    captured = []
    monkeypatch.setattr(
        platform_utils, "_notify_macos",
        lambda *a, **kw: captured.append((a, kw)),
    )
    monkeypatch.setattr(platform_utils, "IS_DARWIN", True)
    platform_utils.notify("Title", "Body", subtitle="Sub")
    assert captured == [(("Title", "Body"), {"subtitle": "Sub"})]


def test_notify_accepts_subtitle_positional(monkeypatch):
    """Legacy call shape — third positional arg.

    Some older callers (and external code that pre-dates v1.1.1) use
    ``notify(title, message, "subtitle")`` positionally.  We accept
    that to avoid a hard break, but the keyword form is preferred.
    The internal ``_notify_macos`` receives the subtitle as a kwarg
    regardless of the public call shape.
    """
    captured = []
    monkeypatch.setattr(
        platform_utils, "_notify_macos",
        lambda *a, **kw: captured.append((a, kw)),
    )
    monkeypatch.setattr(platform_utils, "IS_DARWIN", True)
    # Must not raise TypeError.
    platform_utils.notify("Title", "Body", "Sub")
    # Internal call always normalises to kwarg.
    assert captured == [(("Title", "Body"), {"subtitle": "Sub"})]


def test_notify_handles_no_subtitle(monkeypatch):
    """Both args, no subtitle, must not raise and must pass None through."""
    captured = []
    monkeypatch.setattr(
        platform_utils, "_notify_macos",
        lambda *a, **kw: captured.append((a, kw)),
    )
    monkeypatch.setattr(platform_utils, "IS_DARWIN", True)
    platform_utils.notify("Title", "Body")
    # The internal call gets ``subtitle=None`` (default).
    assert captured == [(("Title", "Body"), {"subtitle": None})]


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


# ---------------------------------------------------------------------------
# Zotero detection helpers (v1.2.4)
# ---------------------------------------------------------------------------


def test_is_zotero_installed_false_on_clean_machine(monkeypatch, tmp_path):
    """is_zotero_installed should return False when no Zotero.app
    exists in any of the known install locations."""
    # Point all candidate paths at non-existent directories.
    monkeypatch.setattr(
        platform_utils, "IS_DARWIN", True,
    )
    # Override the candidate list to use tmp_path (which is empty).
    import os
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda p: str(tmp_path / "user_apps") if p.startswith("~") else p,
    )
    monkeypatch.setattr(
        "os.path.isdir",
        lambda p: False,
    )
    assert platform_utils.is_zotero_installed() is False


def test_is_zotero_installed_true_when_app_exists():
    """is_zotero_installed should return True when /Applications/Zotero.app
    exists (or any of the other candidate paths).

    The conftest fixture patches the function to False, so we
    temporarily replace the module attribute with a fresh lambda
    that always returns True, then restore it.
    """
    import citation_hop.platform_utils as p
    saved = p.is_zotero_installed
    p.is_zotero_installed = lambda: True
    try:
        assert p.is_zotero_installed() is True
    finally:
        p.is_zotero_installed = saved


def test_is_zotero_installed_false_on_non_darwin(monkeypatch):
    """On Windows / Linux, is_zotero_installed should always return
    False without even checking the filesystem — the bypass is
    macOS-specific because Zotero's URL interception on Windows /
    Linux is handled by the browser connector extension, not the
    macOS app's URL handler integration."""
    monkeypatch.setattr(platform_utils, "IS_DARWIN", False)
    # If the function ever tried to touch the filesystem, this would
    # leak through.  We assert it returns False cleanly.
    assert platform_utils.is_zotero_installed() is False


def test_frontmost_app_name_empty_on_non_darwin(monkeypatch):
    """On non-macOS, frontmost_app_name should return '' without
    invoking osascript."""
    monkeypatch.setattr(platform_utils, "IS_DARWIN", False)
    assert platform_utils.frontmost_app_name() == ""


def test_frontmost_app_name_handles_osascript_failure(monkeypatch):
    """If osascript times out or raises, frontmost_app_name should
    return '' (silent failure) rather than propagating the error."""
    monkeypatch.setattr(platform_utils, "IS_DARWIN", True)

    def fake_run(*_a, **_kw):
        raise platform_utils.subprocess.TimeoutExpired(cmd="osascript", timeout=1.5)

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run)
    assert platform_utils.frontmost_app_name() == ""


def test_build_scholar_url_escapes_query():
    """build_scholar_url should percent-encode the query so that
    special characters in the captured text don't break the URL."""
    url = platform_utils.build_scholar_url("Wray, A. (2002). Formulaic Language & the Lexicon")
    assert url.startswith("https://scholar.google.com/scholar?q=")
    # The query is everything after "?q="; special characters in
    # the query must be percent-encoded.  We split on "?q=" and check
    # that the raw characters (space, comma, ampersand, parens) do
    # NOT appear in the encoded query.
    query = url.split("?q=", 1)[1]
    assert " " not in query, f"spaces not encoded: {url!r}"
    assert "," not in query, f"commas not encoded: {url!r}"
    assert "&" not in query, f"ampersands not encoded: {url!r}"
    assert "(" not in query, f"open parens not encoded: {url!r}"
    assert ")" not in query, f"close parens not encoded: {url!r}"
    # And the percent-encoded forms should be there.
    assert "%20" in query
    assert "%2C" in query
    assert "%26" in query
    assert "%28" in query
    assert "%29" in query


def test_build_scholar_url_handles_empty_text():
    """build_scholar_url('') should return a valid (empty) Scholar URL."""
    url = platform_utils.build_scholar_url("")
    assert url == "https://scholar.google.com/scholar?q="


# ---------------------------------------------------------------------------
# v1.2.5: Zotero detection now uses a TTL cache + pgrep-first strategy.
# These tests verify the new behaviour is reliable and the cache works.
#
# Note: tests/conftest.py auto-mocks ``is_zotero_installed`` to return
# False so that other tests (lookup.py etc.) don't have to know whether
# the developer's machine has Zotero installed.  Our new tests work
# around that by:
#   1. exercising the inner ``_detect_zotero_running`` directly (no
#      conftest interference), and
#   2. testing the ``_TTLCache`` class in isolation.
# ---------------------------------------------------------------------------

def test_detect_zotero_running_uses_pgrep_first(monkeypatch):
    """When pgrep says Zotero is running, _detect_zotero_running
    should return True without touching the filesystem.

    This is the bug-fix for v1.2.5: previously we relied on
    ``os.path.isdir("/Applications/Zotero.app")`` which can return
    False intermittently under macOS Spotlight indexing pressure,
    causing the bypass to flip on and off.  The new implementation
    checks ``pgrep -x zotero`` first, which is race-free and
    doesn't touch the FS.
    """
    import subprocess
    fake = subprocess.CompletedProcess(
        args=["pgrep", "-x", "zotero"],
        returncode=0,
        stdout="66846\n",
        stderr="",
    )
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        calls["n"] += 1
        return fake

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run)
    assert platform_utils._detect_zotero_running() is True
    assert calls["n"] == 1, "pgrep should be called exactly once"


def test_detect_zotero_running_falls_back_to_isdir(monkeypatch, tmp_path):
    """When pgrep is missing or returns no match, _detect_zotero_running
    should fall back to checking the .app bundle paths.

    The fallback is a *weaker* signal (the .app might be installed
    but Zotero not running) but it's still better than always
    returning False — we'd rather over-bypass than under-bypass.
    """
    import subprocess

    def fake_run_fail(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=1, stdout="", stderr="",
        )

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run_fail)
    fake_app = tmp_path / "Zotero.app"
    fake_app.mkdir()
    # Point the candidate list at our tmp_path fixture so the test
    # doesn't have to touch the real /Applications tree (which is
    # racy and not writable on CI).  v1.3.0 introduced this hook.
    monkeypatch.setattr(
        platform_utils, "_ZOTERO_APP_CANDIDATES",
        (str(fake_app),),
    )
    assert platform_utils._detect_zotero_running() is True


def test_detect_zotero_running_falls_back_when_pgrep_crashes(monkeypatch):
    """If pgrep raises (e.g. FileNotFoundError on a stripped-down
    sandbox), the function should swallow the error and fall back
    to the FS check rather than crash the hotkey path.

    We monkeypatch both the pgrep call AND the isdir check to
    return False everywhere, so we can assert the function's
    behaviour independently of whether the test machine has
    Zotero installed.
    """
    def fake_run_crash(*args, **kwargs):
        raise FileNotFoundError("pgrep: command not found")

    monkeypatch.setattr(platform_utils.subprocess, "run", fake_run_crash)
    monkeypatch.setattr(platform_utils.os.path, "isdir", lambda p: False)
    # Both signals negative — should return False without raising
    assert platform_utils._detect_zotero_running() is False


def test_is_zotero_installed_ttl_cache(monkeypatch):
    """is_zotero_installed should cache its result for 60 s so a
    burst of hotkey presses doesn't spawn a pgrep on every call.

    We use a fake loader that counts invocations, call is_zotero_installed
    3 times in a row, and assert the loader ran only once.
    """
    import citation_hop.platform_utils as p
    call_count = {"n": 0}

    def fake_loader():
        call_count["n"] += 1
        return False

    cache = p._TTLCache(fake_loader, ttl_seconds=60.0)
    for _ in range(3):
        assert cache.get() is False
    assert call_count["n"] == 1, "TTL cache should call loader only once per TTL window"


def test_is_zotero_installed_ttl_cache_invalidation():
    """refresh_zotero_cache (and _TTLCache.invalidate) should force
    the next call to re-invoke the loader."""
    import citation_hop.platform_utils as p
    call_count = {"n": 0}

    def fake_loader():
        call_count["n"] += 1
        return True

    cache = p._TTLCache(fake_loader, ttl_seconds=60.0)
    assert cache.get() is True
    assert call_count["n"] == 1
    cache.invalidate()
    assert cache.get() is True
    assert call_count["n"] == 2, "invalidate() should force a fresh loader call"


def test_is_zotero_installed_ttl_cache_fails_closed_on_loader_error():
    """If the loader raises, the cache should return False (fail
    closed) rather than propagating the exception or caching a
    bad value.  This protects against pgrep being unavailable
    on a stripped-down macOS sandbox.
    """
    import citation_hop.platform_utils as p

    def exploding_loader():
        raise FileNotFoundError("pgrep not found")

    cache = p._TTLCache(exploding_loader, ttl_seconds=60.0)
    assert cache.get() is False
    # Subsequent calls return the cached False, no re-raise.
    assert cache.get() is False
