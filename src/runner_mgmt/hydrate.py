"""Hydration writer + aiodocker mount builder. Plan task D4 (AC16).

B-INFRA-3 fix: switched from a host-path bind mount to a shared named
volume. The previous design wrote `/tmp/hydration-<rid>.json` inside the
backend container and passed that container-local path to aiodocker as
a bind source — but aiodocker forwards the path to the host Docker
daemon, which resolves it host-side. The file lived in the backend
container, not the host, so every spawn 400'd with "bind source path
does not exist".

The new design:

  * docker-compose mounts named volume `kloc-hydration` into the backend
    at `/tmp/kloc-hydration` (RW) and is declared as a top-level volume.
  * `write_hydration_tempfile` writes
    `/tmp/kloc-hydration/<runner_id>.json` inside the backend.
  * `build_hydration_mount` returns a `Type: volume` entry mounting the
    SAME named volume at `/run/kloc` inside each runner container (RO).
  * Runner reads `/run/kloc/<runner_id>.json` — the path that is now
    exported via `KLOC_HYDRATION_PATH` env (per-spawn, not the legacy
    fixed `/run/kloc/hydration.json` shared-name path).

The volume is shared by reference (Docker storage layer), so no
host-path resolution is needed and the same content surfaces under
the runner's mountpoint immediately."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Backend-side directory inside the backend container where hydration
# files are written. Backed by named volume `kloc-hydration` (see
# `docker-compose.yml`). Read lazily so test/operator env overrides set
# AFTER module import are honoured.
_DEFAULT_HYDRATION_BACKEND_DIR = "/tmp/kloc-hydration"


def _hydration_backend_dir() -> Path:
    return Path(
        os.environ.get(
            "KLOC_HYDRATION_BACKEND_DIR", _DEFAULT_HYDRATION_BACKEND_DIR
        )
    )


# Runner-side directory where the same named volume surfaces. The
# per-spawn filename is `<runner_id>.json`; the absolute path is
# exposed to the runner via `KLOC_HYDRATION_PATH` env at spawn time.
HYDRATION_RUNNER_DIR = "/run/kloc"


def _hydration_volume_name() -> str:
    return os.environ.get("KLOC_HYDRATION_VOLUME", "kloc-hydration")


# Module-level `__getattr__` lets older import sites
# (`from src.runner_mgmt.hydrate import HYDRATION_VOLUME_NAME`) keep
# working while the actual value is resolved lazily on every access.
# Tests + tooling that set the env after import now see the new value.
def __getattr__(name: str):
    if name == "HYDRATION_VOLUME_NAME":
        return _hydration_volume_name()
    if name == "HYDRATION_BACKEND_DIR":
        return _hydration_backend_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _backend_path_for(runner_id: str) -> Path:
    return _hydration_backend_dir() / f"{runner_id}.json"


def runner_mount_path_for(runner_id: str) -> str:
    """Path inside the runner container where the hydration file
    appears via the shared named volume. Goes into the runner env as
    `KLOC_HYDRATION_PATH`."""
    return f"{HYDRATION_RUNNER_DIR}/{runner_id}.json"


def write_hydration_tempfile(runner_id: str, payload: Any) -> Path:
    """Serialize `payload` (HydrationPayload Pydantic model OR plain
    dict) to `/tmp/kloc-hydration/<runner_id>.json` inside the backend
    container. The same file surfaces inside the runner container under
    `/run/kloc/<runner_id>.json` via the shared named volume.

    Creates the directory tree if missing — first-spawn-after-boot will
    need this when the named volume is fresh."""
    _hydration_backend_dir().mkdir(parents=True, exist_ok=True)
    path = _backend_path_for(runner_id)
    if hasattr(payload, "model_dump_json"):
        body = payload.model_dump_json()
    else:
        body = json.dumps(payload)
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        log.warning("hydration.chmod_failed", extra={"path": str(path)})
    return path


def cleanup_hydration_tempfile(runner_id: str) -> None:
    """Called from DockerRunner.terminate after container.wait() returns
    (Contract D §549). Removes the per-runner file from the shared
    named volume."""
    path = _backend_path_for(runner_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("hydration.unlink_failed", extra={"path": str(path)})


def build_hydration_mount() -> dict:
    """aiodocker `HostConfig.Mounts` entry: named volume, read-only.

    Mounting the directory (not the individual file) means every runner
    container can see every pending hydration file on the volume — but
    each runner reads only the file at its own `KLOC_HYDRATION_PATH`
    env, so there's no cross-pollination. Cleaner than per-file mounts
    and avoids the legacy host-path resolution bug entirely (B-INFRA-3)."""
    return {
        "Type": "volume",
        "Source": _hydration_volume_name(),
        "Target": HYDRATION_RUNNER_DIR,
        "ReadOnly": True,
    }


def build_skills_mount(host_skills_dir: str) -> dict:
    """Skills directory mount via shared named volume `kloc-skills`.

    B-INFRA-3 audit point 1: the legacy bind-mount of `Path(host_skills
    _dir).resolve()` had the same host/container path-resolution bug as
    the hydration tempfile — `./skills` resolved inside the backend
    container to `/app/skills`, which the host Docker daemon couldn't
    find. The named volume approach matches the hydration fix; compose
    seeds the volume from the repo root via an init container (see
    `docker-compose.yml` mc-init-style skill seeder)."""
    return {
        "Type": "volume",
        "Source": os.environ.get("KLOC_SKILLS_VOLUME", "kloc-skills"),
        "Target": "/skills",
        "ReadOnly": True,
    }
