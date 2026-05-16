"""CORS middleware contract tests.

Pin the behavior added to `src/main.create_app` so the Next.js UI at
`http://localhost:3000` can talk to the FastAPI backend:

  - Browser preflight `OPTIONS /v1/*` MUST be answered by Starlette's
    `CORSMiddleware` with 200/204 and the standard `access-control-*`
    response headers — NOT fall through to a 405 from the router.

  - Cross-origin actual requests (GET / POST with an `Origin` header that
    matches the configured allow-list) MUST echo `access-control-allow-
    origin` back so the browser doesn't drop the response.

These tests drive the real `app` from `src.main` via FastAPI's TestClient
WITHOUT entering the lifespan context (no Postgres / S3 / Docker needed
for routing-level assertions). Env vars set before import keep boot-time
settings validation happy.
"""
from __future__ import annotations

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit


ORIGIN = "http://localhost:3000"


@pytest.fixture(scope="module", autouse=True)
def _stub_mode_for_module():
    # Boot-time `Settings` validates the configured LLM provider has a
    # key. These tests don't talk to an LLM, so set stub mode for the
    # duration of the module. Using `MonkeyPatch.context()` (and not
    # `os.environ.setdefault` at module scope) so the env mutation is
    # rolled back after the last test in this module rather than
    # leaking into unrelated tests in the same pytest invocation.
    mp = MonkeyPatch()
    mp.setenv("KLOC_STUB_MODE", "true")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def client(_stub_mode_for_module) -> TestClient:
    # Import `app` lazily inside the fixture so the import-time read of
    # `Settings` happens AFTER `KLOC_STUB_MODE` is set above; importing
    # at module scope would lock in the env state from whatever ran
    # before this file in the test session.
    from src.main import app
    from src.settings import get_settings

    get_settings.cache_clear()

    # TestClient used WITHOUT `with ... as`: Starlette will not start the
    # lifespan context, so the test is pure routing/middleware and doesn't
    # require Postgres / MinIO / Docker.
    return TestClient(app)


def test_options_preflight_returns_204_or_200_with_cors_headers(
    client: TestClient,
) -> None:
    """Preflight for `POST /v1/sessions` must be answered by CORSMiddleware.

    Reproduces the bug ticket: the curl
        curl -X OPTIONS http://localhost:8002/v1/sessions
             -H 'origin: http://localhost:3000'
             -H 'access-control-request-method: POST'
    used to return 405 from FastAPI's default router. With CORSMiddleware
    installed it must return 200/204 with `access-control-allow-origin`
    echoing the request origin and `access-control-allow-methods`
    advertising POST.
    """
    resp = client.options(
        "/v1/sessions",
        headers={
            "origin": ORIGIN,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )

    assert resp.status_code in (200, 204), (
        f"expected 200/204 from CORS preflight, got {resp.status_code}; "
        f"body={resp.text!r}"
    )
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin == ORIGIN, (
        f"expected access-control-allow-origin={ORIGIN!r}, "
        f"got {allow_origin!r}"
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods.upper(), (
        f"expected POST in access-control-allow-methods, got {allow_methods!r}"
    )


def test_post_response_includes_cors_header(client: TestClient) -> None:
    """Actual cross-origin request must carry `access-control-allow-origin`.

    The spec says "POST with origin header to an existing simple endpoint
    like /healthz (GET; adapt: send GET with origin header)". `/healthz`
    is the only zero-dependency route (no DB / S3 / runner registry) so
    it works without lifespan being started.
    """
    resp = client.get("/healthz", headers={"origin": ORIGIN})

    assert resp.status_code == 200, (
        f"/healthz should be 200; got {resp.status_code}, body={resp.text!r}"
    )
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin is not None, (
        "response is missing access-control-allow-origin header; "
        f"headers={dict(resp.headers)!r}"
    )
    assert allow_origin == ORIGIN, (
        f"expected access-control-allow-origin={ORIGIN!r}, "
        f"got {allow_origin!r}"
    )
