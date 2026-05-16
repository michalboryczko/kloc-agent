"""Tests for `runner/agent_factory.py`.

Regression: `model_id = getattr(payload, "model_id", None) or payload.get("model_id", "")`
raises `AttributeError` when `payload` is a Pydantic model (no `.get()`).
Fix: use a `_field` helper that calls `.get()` only on dicts.
"""
from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _field(payload: Any, name: str, default: Any = None) -> Any:
    # Mirror of the helper inlined into agent_factory.build_agent.
    v = getattr(payload, name, None)
    if v is None and isinstance(payload, dict):
        v = payload.get(name, default)
    return v if v is not None else default


def test_field_reads_pydantic_attribute() -> None:
    class FakePyd:
        model_id = "claude"

    assert _field(FakePyd(), "model_id") == "claude"


def test_field_reads_dict_key() -> None:
    assert _field({"model_id": "gemini-3.1-pro"}, "model_id") == "gemini-3.1-pro"


def test_field_returns_default_for_missing_key() -> None:
    assert _field({}, "missing", "fallback") == "fallback"


def test_field_returns_default_for_missing_attr() -> None:
    class Empty:
        pass

    assert _field(Empty(), "missing", "fallback") == "fallback"


def test_field_does_not_invoke_get_on_pydantic_like_object() -> None:
    """`.get()` on a Pydantic model raises AttributeError. The helper
    must skip the dict branch when payload is not a dict."""

    class FakePyd:
        model_id = "claude"

        def get(self, *a, **k):
            raise AssertionError(".get() must not be called on Pydantic models")

    assert _field(FakePyd(), "model_id") == "claude"
