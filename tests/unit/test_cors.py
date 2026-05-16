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

import os

# Boot-time `Settings` validates the configured LLM provider has a key.
# Tests don't talk to an LLM — flip to stub mode before importing main.
os.environ.setdefault("KLOC_STUB_MODE", "true")

import pytest
from fastapi.testclient import TestClient

from src.main import app


pytestmark = pytest.mark.unit


ORIGIN = "http://localhost:3000"


@pytest.fixture(scope="module")
def client() -> TestClient:
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
