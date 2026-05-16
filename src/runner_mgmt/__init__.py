"""Runner management package.

Public surface:
- `RunnerRegistry` — per-session container registry
- `sweeper.orphan_sweep` — boot-time orphan-container sweep
"""

from src.runner_mgmt.registry import RunnerRegistry  # noqa: F401
from src.runner_mgmt import sweeper  # noqa: F401

__all__ = ["RunnerRegistry", "sweeper"]
