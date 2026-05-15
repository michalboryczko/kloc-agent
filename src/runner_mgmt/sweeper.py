"""Boot-time orphan-container sweeper. Plan task D9 (AC25).

Lists containers with label `kloc.role=runner`. Any container NOT in the
backend's `RunnerRegistry` at boot is killed + removed. This is the
defence against "backend crashed, runners survived" -- the runner is
stateless so the cleanest path is to wipe orphans and let users resume.

Call sites:
- `src/main.py` lifespan: `await sweeper.orphan_sweep(settings)` at boot,
  before yielding. At boot the registry is fresh (no `known_runner_ids`),
  so every surviving runner is by definition orphaned and gets reaped.
- Tests / ad-hoc: pass `known_runner_ids=registry.known_runner_ids()`.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

try:
    import aiodocker  # type: ignore
except ImportError:
    aiodocker = None  # type: ignore

log = logging.getLogger(__name__)


async def orphan_sweep(
    settings_or_known: Any | None = None,
    known_runner_ids: Iterable[str] | None = None,
) -> int:
    """Kill+delete every container labelled `kloc.role=runner` whose
    `kloc.runner_id` label is not present in `known_runner_ids`.

    At backend startup `known_runner_ids` is empty (the in-process
    registry is fresh), so this kills every surviving runner -- which
    is the correct behaviour: surviving runners are by definition
    orphaned because the registry that owned them just died.

    Returns the count of containers swept."""
    if aiodocker is None:
        log.warning("orphan_sweep.skipped_no_aiodocker")
        return 0
    # Back-compat: dev-1's lifespan passes the Settings object as the
    # first positional arg; the actual ID set is the optional second.
    # When called with only `known_runner_ids=[...]`, accept that too.
    if known_runner_ids is None and isinstance(settings_or_known, Iterable) and not hasattr(
        settings_or_known, "model_dump"
    ):
        known_runner_ids = settings_or_known  # type: ignore[assignment]
    known = set(known_runner_ids or ())
    docker = aiodocker.Docker()
    try:
        containers = await docker.containers.list(
            all=True, filters={"label": ["kloc.role=runner"]}
        )
        swept = 0
        for container in containers:
            # aiodocker's `containers.list()` returns lightweight Container
            # objects; the labels are returned in `containers.list()`'s
            # response but only via a public-API path. Prefer
            # `container.show()` (issues a GET /containers/{id}/json and
            # is the documented way to fetch full inspect data).
            try:
                data = await container.show()
            except Exception:
                log.exception("orphan_sweep.show_failed")
                continue
            labels = (data.get("Config") or {}).get("Labels") or {}
            runner_id = labels.get("kloc.runner_id")
            if runner_id and runner_id in known:
                continue
            try:
                await container.stop(t=5)
            except Exception:
                pass
            try:
                await container.delete(force=True)
                swept += 1
                log.info(
                    "orphan_sweep.removed",
                    extra={
                        "runner_id": runner_id,
                        "container_id": container.id,
                    },
                )
            except Exception:
                log.exception("orphan_sweep.delete_failed")
        return swept
    finally:
        await docker.close()
