"""Runner management package.

Public surface (used by dev-1's `src/main.py` lifespan):
- `RunnerRegistry` — per-session container registry
- `DockerRunner` — aiodocker-based concrete `Runner` impl
- `sweeper.orphan_sweep` — boot-time orphan-container sweep (AC25)

dev-1 imports `RunnerRegistry` and `sweeper` here. Other names are
re-exported for explicitness."""

from src.runner_mgmt.registry import RunnerRegistry  # noqa: F401
from src.runner_mgmt import sweeper  # noqa: F401

__all__ = ["RunnerRegistry", "sweeper"]
