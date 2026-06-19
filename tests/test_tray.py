"""Tests for the tray / hotkey dispatch logic.

These tests focus on the threading model and the crash-mitigation
plumbing (signal handlers, IS_TRUSTED check, off-thread hotkey
work) — pieces of code that are hard to exercise in CI but cheap
to pin down with mocks.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from citation_hop import tray


@pytest.fixture(autouse=True)
def _reset_hotkey_worker():
    """The global _HOTKEY_WORKER slot is process-wide; reset it
    before and after each test so they don't pollute each other."""
    tray._HOTKEY_WORKER = None
    yield
    # Wait briefly for any spawned worker to finish.
    if tray._HOTKEY_WORKER is not None and tray._HOTKEY_WORKER.is_alive():
        tray._HOTKEY_WORKER.join(timeout=2.0)
    tray._HOTKEY_WORKER = None


# ---------------------------------------------------------------------------
# Hotkey dispatch
# ---------------------------------------------------------------------------
#
# v1.1.1: the hotkey handler must return immediately.  All the real
# work (get_selection, lookup, webbrowser.open) goes onto a worker
# thread so pynput's CFRunLoop can return to its loop without
# re-entering the HID event tap (which on macOS 15 / Apple Silicon
# produces a SIGILL).

def _make_tray():
    """Build a CitationHopTray without pystray actually starting."""
    # Avoid touching the live pystray Icon; we only test _on_hotkey.
    t = tray.CitationHopTray.__new__(tray.CitationHopTray)
    t.cfg = {
        "hotkey": "<cmd>+<shift>+l",
        "mailto": "test@example.com",
        "engines": [],
    }
    t.enabled = True
    t.icon = MagicMock()
    t._listener = MagicMock()
    return t


def test_on_hotkey_dispatches_to_worker(monkeypatch):
    """Pressing the hotkey must return immediately and spawn a thread."""
    t = _make_tray()

    # Spy on the worker method.
    started = threading.Event()
    entered = threading.Event()

    def fake_worker():
        entered.set()
        started.set()

    monkeypatch.setattr(t, "_do_hotkey_work", fake_worker)

    # Hotkey press should not block and should spawn a thread.
    t0 = time.monotonic()
    t._on_hotkey()
    elapsed = time.monotonic() - t0

    # The handler must return in well under the 120ms sleep in
    # get_selection — that's the keystone of the re-entrancy fix.
    assert elapsed < 0.05, (
        f"_on_hotkey blocked for {elapsed*1000:.0f}ms; must be <50ms "
        f"to avoid re-entering pynput's CFRunLoop on macOS"
    )

    # The worker must have actually run.
    assert entered.wait(1.0), "worker thread never started"


def test_on_hotkey_drops_when_disabled():
    """If the user disabled the app, the hotkey is a silent no-op."""
    t = _make_tray()
    t.enabled = False

    # No thread should be spawned; we observe by checking the global
    # worker slot is still None (it was None to start with).
    assert tray._HOTKEY_WORKER is None
    t._on_hotkey()
    # The slot must not have been populated.
    assert tray._HOTKEY_WORKER is None


def test_on_hotkey_drops_duplicate_press(monkeypatch):
    """If a hotkey worker is still running, a second press is dropped
    rather than queued.  Prevents accidental re-entry if AppleScript
    or pyperclip briefly stalls."""
    t = _make_tray()

    hold = threading.Event()
    release = threading.Event()
    entered_count = [0]

    def slow_worker():
        entered_count[0] += 1
        hold.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr(t, "_do_hotkey_work", slow_worker)

    # First press: starts the slow worker.
    t._on_hotkey()
    assert hold.wait(1.0), "first worker did not start"

    # Second press while first is still running: must be dropped.
    t._on_hotkey()
    assert entered_count[0] == 1, (
        f"second press should have been dropped; entered_count={entered_count[0]}"
    )

    # Let the first worker finish so we don't leak a thread.
    release.set()


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def test_install_signal_handlers_does_not_raise(monkeypatch):
    """The installer must be a safe no-op on platforms where signals
    can't be installed (Windows for SIGILL, non-main-thread contexts,
    etc.)."""
    # Save and restore real handlers.
    real = {sig: signal.getsignal(sig) for sig in tray._FATAL_SIGNAL_HINTS}
    try:
        # Should not raise even if signal.signal rejects.
        tray._install_signal_handlers()
    finally:
        for sig, handler in real.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass


def test_signal_handler_writes_hint_and_escalates(monkeypatch):
    """When a fatal signal fires, our handler must write the hint to
    stderr and then escalate to the default disposition (so the
    process still dies — we just add context first)."""
    # Use SIGUSR1 (always present on POSIX, never raised by the OS)
    # and craft a fake handler that points at our real one.
    from citation_hop import tray as _t

    captured = []

    monkeypatch.setattr(sys, "stderr", MagicMock(write=lambda s: captured.append(s)))

    handler = _t._make_signal_handler(
        signal.SIGUSR1,
        "synthetic hint for the test",
    )
    # We don't want to actually kill the test runner; patch os.kill
    # to record the call, and patch signal.signal to record re-arms.
    killed = []
    rearmed = []
    monkeypatch.setattr(_t.os, "kill", lambda pid, sig: killed.append(sig))
    monkeypatch.setattr(_t.signal, "signal", lambda s, h: rearmed.append(s))

    handler(signal.SIGUSR1, None)

    assert any("synthetic hint" in s for s in captured), (
        f"expected hint in stderr; got {captured!r}"
    )
    assert killed == [signal.SIGUSR1], (
        f"expected escalation via os.kill; got {killed!r}"
    )
    # The handler must restore default disposition.
    assert rearmed, "handler should re-arm default disposition"


# ---------------------------------------------------------------------------
# IS_TRUSTED / Accessibility check
# ---------------------------------------------------------------------------

def test_check_trust_warns_when_not_trusted(monkeypatch):
    """If pynput reports IS_TRUSTED = False, _check_trust_and_warn
    must log a warning (so the user can see it in stdout)."""
    from citation_hop import tray as _t

    listener = MagicMock()
    listener.IS_TRUSTED = False

    captured = []
    fake_notify = lambda *a, **kw: captured.append((a, kw))
    monkeypatch.setattr(_t, "notify", fake_notify)

    # Patch time.sleep to be a no-op (we don't want to actually wait).
    monkeypatch.setattr(_t.time, "sleep", lambda *_a, **_kw: None)

    # Patch logging to capture the warning.
    import logging
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    cap = _Capture(logging.WARNING)
    cap.setLevel(logging.WARNING)
    _t.LOG.addHandler(cap)
    try:
        _t._check_trust_and_warn(listener)
    finally:
        _t.LOG.removeHandler(cap)

    assert any("Accessibility" in r.getMessage() for r in records), (
        f"expected Accessibility warning; got {[r.getMessage() for r in records]!r}"
    )


def test_check_trust_silent_when_trusted(monkeypatch):
    """If pynput reports IS_TRUSTED = True, no warning, no notification."""
    from citation_hop import tray as _t

    listener = MagicMock()
    listener.IS_TRUSTED = True

    fake_notify = MagicMock()
    monkeypatch.setattr(_t, "notify", fake_notify)
    monkeypatch.setattr(_t.time, "sleep", lambda *_a, **_kw: None)

    _t._check_trust_and_warn(listener)

    fake_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Worker thread exception safety
# ---------------------------------------------------------------------------

def test_do_hotkey_work_swallows_exceptions(monkeypatch):
    """If any step in the worker raises, the thread must not die
    loudly — the exception should be logged and the listener kept
    alive for the next press."""
    from citation_hop import tray as _t

    t = _make_tray()
    # Force get_selection to raise.
    monkeypatch.setattr(_t, "get_selection", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    # Patch notify so the exception path is exercised.
    monkeypatch.setattr(_t, "notify", lambda *a, **kw: None)

    # Patch LOG to capture.
    import logging
    records = []

    class _Cap(logging.Handler):
        def emit(self, record):
            records.append(record)

    cap = _Cap(logging.ERROR)
    _t.LOG.addHandler(cap)
    try:
        t._do_hotkey_work()
    finally:
        _t.LOG.removeHandler(cap)

    assert any("Selection error" in r.getMessage() or "get_selection" in r.getMessage()
               for r in records), (
        f"expected selection error to be logged; got {[r.getMessage() for r in records]!r}"
    )


def test_do_hotkey_work_calls_notify_with_subtitle_kwarg(monkeypatch):
    """Regression test: v1.1.0 had a bug where _do_hotkey_work
    called ``notify(title, message, "subtitle as 3rd positional")``,
    but ``notify``'s third argument is keyword-only.  Python raised
    TypeError, the except inside notify caught it, and the
    "DOI copied" subtitle never appeared.  This test pins the
    correct call shape."""
    from citation_hop import tray as _t

    t = _make_tray()
    monkeypatch.setattr(_t, "get_selection", lambda: "Smith, J. (2020). A paper. doi:10.1234/abc")
    monkeypatch.setattr(_t, "copy_to_clipboard", lambda *_a, **_kw: None)

    # Spy on notify.
    captured = []
    monkeypatch.setattr(_t, "notify", lambda *a, **kw: captured.append((a, kw)))

    # We need lookup() to actually run and return a "doi" status with
    # an engine_used.  The default engines from .engines.default_engines()
    # give us a usable list.  Easiest: pre-populate the cfg with a
    # minimal engine list that resolves to a DOI.
    from citation_hop.engines import Engine
    t.cfg["engines"] = [
        # A simple doi_url engine (no network calls).
        Engine(id="doi_org", name="doi.org", stage="doi_url", enabled=True,
               url_template="https://doi.org/{doi}"),
    ]

    t._do_hotkey_work()

    # Find the DOI notification.
    doi_calls = [c for c in captured if len(c[0]) >= 2 and "DOI" in str(c[0][1])]
    assert doi_calls, f"no DOI notification fired; captured={captured!r}"
    args, kwargs = doi_calls[0]
    # Title, message positional; subtitle must be a kwarg, not a 3rd positional.
    assert "subtitle" in kwargs, (
        f"subtitle must be passed as kwarg, not positional 3rd arg; "
        f"got args={args!r} kwargs={kwargs!r}"
    )
    assert "DOI copied" in kwargs["subtitle"]
