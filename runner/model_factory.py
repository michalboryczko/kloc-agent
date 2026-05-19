"""Model provider switch.

Constraint 4: Strands silently defaults to Bedrock; we ALWAYS pass an
explicit model. PoC supports gemini only — any other provider raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def create_model(model_id: str, llm_provider: str | None = None) -> Any:
    """Returns a Strands `Model` instance configured for Gemini.

    Raises `ValueError` for any non-gemini `llm_provider` so a misconfig
    fails loudly instead of silently routing to Bedrock or another stub.
    """
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if provider == "gemini":
        from strands.models.gemini import GeminiModel  # type: ignore

        # GeminiModel does not auto-resolve from env — pass the key
        # explicitly. Forwarded via `docker_runner.py` spawn env
        # (GEMINI_API_KEY / GOOGLE_API_KEY).
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY required. Forward it from the backend via "
                "docker_runner spawn env."
            )
        return GeminiModel(
            client_args={"api_key": api_key},
            model_id=model_id,
        )
    raise ValueError(
        f"Unsupported LLM_PROVIDER: {provider!r}. Only 'gemini' is wired."
    )
