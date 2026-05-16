"""Strict-mode HMAC fallback tests for the runner webhook receiver.

Pins the security contract added alongside `Settings.allow_hmac_fallback`:

- Default (`allow_hmac_fallback=False`): if the runner_id is not in the
  registry, the webhook MUST reject the request with 401 BEFORE running
  HMAC verification — even if the body is correctly signed with the
  bootstrap secret (`settings.kloc_hook_secret`). Without this, any
  caller that learns the global bootstrap secret could authenticate as
  any runner_id of their choice.

- Opt-in (`allow_hmac_fallback=True`): the bootstrap secret IS accepted
  when the registry has no entry for the runner_id — restoring the old
  permissive behaviour for dev / stub mode.

These tests stand up a minimal FastAPI app with only the webhooks router
included, override `get_session` with a no-op stub, and inject a fake
runner_registry whose `get_by_runner_id` always returns `None` (the
"registry wired but empty" condition that triggers the strict path).
"""
from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import webhooks
from src.db.deps import get_session
from src.hooks_audit.verify_hmac import sign_for_test
from src.settings import Settings, get_settings


pytestmark = pytest.mark.unit


BOOTSTRAP_SECRET = "bootstrap-secret-for-test-32bytes"


class _FakeRegistry:
    """Stand-in for `RunnerRegistry` whose `get_by_runner_id` returns None.

    This simulates the "registry exists, runner_id unknown" branch — the
    one the strict mode is designed to guard.
    """

    async def get_by_runner_id(self, runner_id: str) -> Any:
        return None


class _FakeAsyncSession:
    """No-op AsyncSession stand-in for the audit / artifact repos.

    The webhook only calls `add` + `flush` + `commit` on the session in
    the BeforeToolCall happy path; everything else is delegated to the
    SQLAlchemy ORM which we don't exercise here.
    """

    def add(self, _row: Any) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


async def _fake_get_session():
    yield _FakeAsyncSession()


def _build_app(*, allow_hmac_fallback: bool) -> FastAPI:
    """Build a minimal FastAPI app with the webhooks router and an
    overridden Settings that flips `allow_hmac_fallback`."""
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/v1")
    app.dependency_overrides[get_session] = _fake_get_session
    app.state.runner_registry = _FakeRegistry()

    # Override the cached settings used by the route handler.
    get_settings.cache_clear()
    override = Settings(
        kloc_hook_secret=BOOTSTRAP_SECRET,
        allow_hmac_fallback=allow_hmac_fallback,
        stub_mode=True,
    )
    app.dependency_overrides[get_settings] = lambda: override

    # The handler imports `get_settings` and calls it directly (not via
    # Depends), so also monkey-patch the cached return value.
    get_settings.cache_clear()

    def _patched() -> Settings:
        return override

    # Swap the symbol the webhook module actually resolves.
    webhooks.get_settings = _patched  # type: ignore[assignment]
    return app


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Each test gets a clean Settings cache and restores the real
    `get_settings` on the webhooks module after running."""
    real_get_settings = webhooks.get_settings
    get_settings.cache_clear()
    try:
        yield
    finally:
        webhooks.get_settings = real_get_settings  # type: ignore[assignment]
        get_settings.cache_clear()


def _signed_request(
    secret: str,
) -> tuple[bytes, dict[str, str]]:
    body_dict = {
        "event": "BeforeToolCall",
        "run_id": "run-unit-1",
        "payload": {"tool_name": "kloc.search", "tool_call_id": "tc-1"},
    }
    body = json.dumps(body_dict).encode("utf-8")
    ts_ms = int(time.time() * 1000)
    sig = sign_for_test(body, ts_ms, secret)
    headers = {
        "Authorization": f"HMAC {sig}",
        "X-Kloc-Hook-Ts": str(ts_ms),
        "X-Kloc-Hook-Event": "BeforeToolCall",
        "Content-Type": "application/json",
    }
    return body, headers


def test_strict_mode_rejects_unknown_runner_even_with_valid_fallback_signature() -> None:
    """Default `allow_hmac_fallback=False`: a body signed with the
    bootstrap secret MUST NOT be accepted when the registry has no
    entry for the runner_id. The receiver short-circuits with 401
    BEFORE running HMAC verification."""
    app = _build_app(allow_hmac_fallback=False)
    body, headers = _signed_request(BOOTSTRAP_SECRET)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/runners/unknown-runner/events",
            content=body,
            headers=headers,
        )

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "unknown runner"


def test_permissive_mode_accepts_fallback_signature_for_unknown_runner() -> None:
    """Opt-in `allow_hmac_fallback=True`: the SAME request now succeeds
    because the bootstrap secret is accepted as the runner's secret."""
    app = _build_app(allow_hmac_fallback=True)
    body, headers = _signed_request(BOOTSTRAP_SECRET)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/webhooks/runners/unknown-runner/events",
            content=body,
            headers=headers,
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["decision"] == "allow"


def test_build_app_settings_construction_does_not_trip_new_validator(
    monkeypatch,
) -> None:
    """Pins the security contract for ISS-07 from the webhook side: the
    Settings() payload used by _build_app must remain constructible
    because BOOTSTRAP_SECRET is non-default. If this test starts failing,
    the validator regressed or _build_app drifted."""
    # stub_mode is bound via validation_alias=KLOC_STUB_MODE; set via env
    # through `monkeypatch` so the cleanup is automatic (raw os.environ
    # mutations leak into later test modules' fixtures).
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_hook_secret=BOOTSTRAP_SECRET,
        allow_hmac_fallback=True,
    )

    assert s.allow_hmac_fallback is True
    assert s.kloc_hook_secret == BOOTSTRAP_SECRET


def test_build_app_settings_with_default_secret_and_no_stub_would_trip(
    monkeypatch,
) -> None:
    """ISS-07: the exact combo the new validator is designed to refuse.
    `gemini_api_key` is set so the *provider-key* validator does not raise
    first and mask the HMAC fallback check."""
    from pydantic import ValidationError

    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "stub-key-for-test")

    with pytest.raises((ValidationError, ValueError)):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            kloc_hook_secret="dev-secret-please-rotate",
            allow_hmac_fallback=True,
        )
