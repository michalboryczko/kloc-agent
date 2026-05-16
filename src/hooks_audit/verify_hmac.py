"""HMAC-SHA256 signing + verification for runner→backend webhooks.

Canonicalization (shared by sender and receiver):

    signing_input = f"{ts}.{body}"           # ts is unix-ms int
    signature_b64 = base64( HMAC_SHA256(secret, signing_input) )
    Authorization = f"HMAC {signature_b64}"

Replay window: 60 s on the receiver. Both `sign_for_test` and
`verify_hmac_signature` live in one module so signers and verifiers
share a single canonical algorithm.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time


REPLAY_WINDOW_MS = 60_000


def sign_for_test(body: bytes, ts: int, secret: str) -> str:
    """Produce a valid `Authorization: HMAC <sig>` header value.

    `ts` is unix-ms (int). `body` is the raw request body bytes.
    Returns just the base64 signature; the caller adds the `HMAC ` prefix.
    """
    payload = f"{ts}.".encode("utf-8") + body
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def verify_hmac_signature(
    body: bytes,
    ts: int,
    sig_b64: str,
    secret: str,
    now_ms: int | None = None,
    replay_window_ms: int = REPLAY_WINDOW_MS,
) -> bool:
    """Constant-time HMAC verify with replay-window guard.

    Returns False on any of: bad base64, signature mismatch, timestamp
    older than `replay_window_ms` or further in the future than the same
    window. Never raises on malformed input.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now - int(ts)) > replay_window_ms:
        return False

    expected = sign_for_test(body, int(ts), secret)
    try:
        given = sig_b64.strip()
        if given.lower().startswith("hmac "):
            given = given[5:].strip()
        # Decode both to compare bytes constant-time.
        expected_bytes = base64.b64decode(expected.encode("ascii"), validate=False)
        given_bytes = base64.b64decode(given.encode("ascii"), validate=False)
    except Exception:
        return False

    return hmac.compare_digest(expected_bytes, given_bytes)
