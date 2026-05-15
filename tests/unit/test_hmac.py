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
