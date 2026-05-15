"""Model provider switch. Plan task D11. Constraint 4: Strands silently
defaults to Bedrock; we ALWAYS pass an explicit model.

`LLM_PROVIDER` env switch: `anthropic` (default), `openrouter` (stub),
`bedrock` (stub). PoC only wires Anthropic."""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def create_model(model_id: str, llm_provider: str | None = None) -> Any:
    """Returns a Strands `Model` instance. Raises if the provider is not
    known. Anthropic is the only fully-wired path in PoC."""
    provider = (llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    if provider == "anthropic":
        from strands.models.anthropic import AnthropicModel  # type: ignore

        # `client_args={"api_key": None}` lets the upstream SDK fall back
        # to ANTHROPIC_API_KEY from env, which the backend passes through
        # to the container at spawn time.
        return AnthropicModel(
            client_args={"api_key": None},
            model_id=model_id,
            max_tokens=4096,
        )
    if provider == "gemini":
        from strands.models.gemini import GeminiModel  # type: ignore

        # Unlike AnthropicModel, GeminiModel does not auto-resolve from
        # env — we pass the key explicitly. Forwarded via
        # `docker_runner.py` spawn env (GEMINI_API_KEY / GOOGLE_API_KEY).
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY required when LLM_PROVIDER=gemini. "
                "Forward it from the backend via docker_runner spawn env."
            )
        return GeminiModel(
            client_args={"api_key": api_key},
            model_id=model_id,
        )
    if provider == "openrouter":
        raise NotImplementedError(
            "openrouter provider stubbed for PoC; set LLM_PROVIDER=anthropic"
        )
    if provider == "bedrock":
        raise NotImplementedError(
            "bedrock provider stubbed for PoC; set LLM_PROVIDER=anthropic"
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
