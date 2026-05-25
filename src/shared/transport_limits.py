"""JSONL-frame byte caps shared by backend ingress and runner egress.

`MAX_LINE_BYTES` is the backend cap; the runner stays one MiB under so
a frame that survives the runner-side check is guaranteed to survive
the backend-side check. Both constants live here so a single edit
changes both — drift between the two has caused the runner to emit
frames its own backend deterministically rejects, which previously
poisoned the channel.
"""
from __future__ import annotations

from typing import Final

MAX_LINE_BYTES: Final[int] = 16 * 1024 * 1024
RUNNER_MAX_FRAME_BYTES: Final[int] = 15 * 1024 * 1024
