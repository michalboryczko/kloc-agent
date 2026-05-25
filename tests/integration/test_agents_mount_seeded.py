"""Integration: the `kloc-agents` named volume reaches the runner.

Spawns one runner via the live backend, execs into the container, and
asserts the seeded `agents/summarizer/AGENT.md` is present and readable
on the runner-side `/agents` mount.

Opt-in via `KLOC_INTEGRATION=1` because it requires the live compose
stack and access to the Docker daemon."""
from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.integration


_INTEGRATION = os.environ.get("KLOC_INTEGRATION") == "1"


@pytest.mark.skipif(
    not _INTEGRATION,
    reason="set KLOC_INTEGRATION=1 to opt in; requires live compose stack",
)
def test_agents_volume_seeded_into_runner(
    compose_stack: None, backend_url: str
) -> None:
    import httpx

    session_id = str(uuid.uuid4())

    with httpx.Client(base_url=backend_url, timeout=30.0) as client:
        r = client.post(
            "/v1/sessions",
            json={"analyst_id": "integration", "title": "agents-mount probe"},
        )
        r.raise_for_status()
        sid = r.json().get("id") or session_id

        with client.stream(
            "POST",
            f"/v1/sessions/{sid}/stream",
            json={
                "thread_id": sid,
                "run_id": str(uuid.uuid4()),
                "messages": [{"role": "user", "content": "ping"}],
            },
        ) as resp:
            # Consume just enough of the SSE body to wait for the runner
            # container to land on the host.
            for i, _line in enumerate(resp.iter_lines()):
                if i > 5:
                    break

    deadline = time.monotonic() + 30
    container_id: str | None = None
    while time.monotonic() < deadline:
        out = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=kloc.session_id={sid}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        if lines:
            container_id = lines[0]
            break
        time.sleep(0.5)

    assert container_id, f"no runner container appeared for session {sid}"

    ls = subprocess.run(
        ["docker", "exec", container_id, "ls", "/agents"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert ls.returncode == 0, f"ls /agents failed: {ls.stderr}"
    assert "summarizer" in ls.stdout.split(), (
        f"expected 'summarizer' under /agents; got {ls.stdout!r}"
    )

    cat = subprocess.run(
        ["docker", "exec", container_id, "cat", "/agents/summarizer/AGENT.md"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert cat.returncode == 0, f"cat AGENT.md failed: {cat.stderr}"
    assert "name: summarizer" in cat.stdout
