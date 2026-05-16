"""HMAC verifier unit tests (Phase 1.C-1.8 / AC11)."""
from __future__ import annotations

import time

import pytest

from src.hooks_audit.verify_hmac import (
    REPLAY_WINDOW_MS,
    sign_for_test,
    verify_hmac_signature,
)


pytestmark = pytest.mark.unit


SECRET = "test-secret-32-bytes-min-base64ish"


def test_sign_and_verify_roundtrip() -> None:
    body = b'{"event":"BeforeToolCall","tool":"kloc.search"}'
    ts = int(time.time() * 1000)
    sig = sign_for_test(body, ts, SECRET)
    assert verify_hmac_signature(body, ts, sig, SECRET, now_ms=ts) is True


def test_authorization_header_prefix_tolerated() -> None:
    body = b"{}"
    ts = int(time.time() * 1000)
    sig = sign_for_test(body, ts, SECRET)
    assert (
        verify_hmac_signature(body, ts, f"HMAC {sig}", SECRET, now_ms=ts)
        is True
    )


def test_tampered_body_rejected() -> None:
    body = b'{"foo":"bar"}'
    ts = int(time.time() * 1000)
    sig = sign_for_test(body, ts, SECRET)
    assert (
        verify_hmac_signature(b'{"foo":"baz"}', ts, sig, SECRET, now_ms=ts)
        is False
    )


def test_wrong_secret_rejected() -> None:
    body = b"{}"
    ts = int(time.time() * 1000)
    sig = sign_for_test(body, ts, SECRET)
    assert (
        verify_hmac_signature(body, ts, sig, SECRET + "x", now_ms=ts) is False
    )


def test_stale_timestamp_rejected_past_window() -> None:
    body = b"{}"
    ts = 1_000_000_000_000
    sig = sign_for_test(body, ts, SECRET)
    now = ts + REPLAY_WINDOW_MS + 1
    assert verify_hmac_signature(body, ts, sig, SECRET, now_ms=now) is False


def test_future_timestamp_rejected_past_window() -> None:
    body = b"{}"
    ts = 1_000_000_000_000
    sig = sign_for_test(body, ts, SECRET)
    now = ts - REPLAY_WINDOW_MS - 1
    assert verify_hmac_signature(body, ts, sig, SECRET, now_ms=now) is False


def test_timestamp_at_window_edge_accepted() -> None:
    body = b"{}"
    ts = 1_000_000_000_000
    sig = sign_for_test(body, ts, SECRET)
    now = ts + REPLAY_WINDOW_MS
    assert verify_hmac_signature(body, ts, sig, SECRET, now_ms=now) is True


def test_garbage_signature_does_not_raise() -> None:
    body = b"{}"
    ts = int(time.time() * 1000)
    assert verify_hmac_signature(body, ts, "@@@not-base64@@@", SECRET) is False
    assert verify_hmac_signature(body, ts, "", SECRET) is False


# ---------------------------------------------------------------------------
# Runner ↔ backend signing-input parity (Critical fix from
# docs/reviews/unmapped-findings.md).
#
# Previously `runner/hooks/audit.py:_sign` built the canonical input as
# `f"{ts}.{body.decode('utf-8')}".encode()` while the verifier in this
# module concatenates raw bytes via `f"{ts}.".encode() + body`. The two
# diverge on non-ASCII utf-8 JSON, causing every BeforeToolCall to fail
# HMAC and silently block tool execution.
# ---------------------------------------------------------------------------


def test_runner_sign_matches_backend_sign_for_ascii_body() -> None:
    from runner.hooks.audit import _sign as runner_sign

    body = b'{"event":"BeforeToolCall","tool":"kloc.search"}'
    ts = 1_700_000_000_000
    assert runner_sign(body, ts, SECRET) == sign_for_test(body, ts, SECRET)


def test_runner_sign_matches_backend_sign_for_unicode_body() -> None:
    """Regression pin: non-ASCII bytes survive the runner→backend HMAC
    round-trip. The byte-concat form is the only one that works for
    arbitrary utf-8 JSON payloads."""
    import json

    from runner.hooks.audit import _sign as runner_sign

    body = json.dumps({"tool": "поиск", "args": {"q": "тест"}}).encode("utf-8")
    ts = 1_700_000_000_000
    assert runner_sign(body, ts, SECRET) == sign_for_test(body, ts, SECRET)


def test_runner_signed_body_verifies_with_backend_verifier() -> None:
    from runner.hooks.audit import _sign as runner_sign

    body = b'{"x":1}'
    ts = int(time.time() * 1000)
    sig = runner_sign(body, ts, SECRET)
    assert verify_hmac_signature(body, ts, sig, SECRET, now_ms=ts) is True
