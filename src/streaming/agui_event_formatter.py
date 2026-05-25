"""Strands -> AG-UI mapping lifted from Repo 2
(chatbot-app/agentcore/src/streaming/agui_event_formatter.py).

Inside our runner the `ag_ui_strands.StrandsAgent` adapter already emits
typed AG-UI events. The runner serialises them to the wire (JSONL) and
the backend re-emits them onto the SSE stream verbatim. This module
therefore handles backend-side normalisation:

  * accept either an `ag_ui.core.events.BaseEvent` Pydantic model OR a
    plain JSONL dict;
  * normalise to the dict form expected by `ag_ui.encoder.EventEncoder`;
  * stamp a timestamp when one is missing (some runner-internal events
    arrive without one)."""

from __future__ import annotations

import time
from typing import Any

try:
    from ag_ui.core.events import BaseEvent  # type: ignore
except ImportError:
    BaseEvent = None  # type: ignore


def normalize(event: Any) -> dict:
    """Coerce any AG-UI event representation into a wire-ready dict.

    Pass-through semantics: unknown keys (e.g. `parentMessageId` on
    `TOOL_CALL_START` / `CUSTOM ToolCallDenied`) survive intact. The
    parent-linking pipeline depends on this — the runner stamps the
    field, the backend forwards it verbatim, the FE reducer groups
    tool cards under the named assistant Message.
    """
    if BaseEvent is not None and isinstance(event, BaseEvent):
        d = event.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(event, dict):
        d = dict(event)
    else:
        raise TypeError(f"Unsupported event type: {type(event)!r}")
    d.setdefault("timestamp", int(time.time() * 1000))
    return d


def is_run_lifecycle_terminal(event: dict) -> bool:
    return event.get("type") in {"RUN_FINISHED", "RUN_ERROR"}
