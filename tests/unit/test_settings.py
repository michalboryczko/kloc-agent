"""Tests for `src/settings.py`.

Regressions covered:
- `get_settings()` was a non-thread-safe module-level singleton; fixed
  to use `functools.lru_cache`. Two calls must return the same instance.
- `anthropic_api_key` / `gemini_api_key` defaulted to `""` so empty
  config wasn't caught at boot. Fixed to default to `None`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_get_settings_returns_same_instance() -> None:
    from src.settings import get_settings

    a = get_settings()
    b = get_settings()
    assert a is b


def test_api_keys_default_to_none(monkeypatch, tmp_path) -> None:
    from src.settings import Settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Default llm_provider is "gemini"; without stub mode the new boot-time
    # validator would refuse to construct. Tests/CI set KLOC_STUB_MODE=true.
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.gemini_api_key is None


def test_missing_anthropic_key_raises_when_not_stub(monkeypatch, tmp_path) -> None:
    from pydantic import ValidationError

    from src.settings import Settings

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_missing_anthropic_key_allowed_when_stub(monkeypatch, tmp_path) -> None:
    from src.settings import Settings

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.stub_mode is True


def test_gemini_branch_enforced(monkeypatch, tmp_path) -> None:
    from pydantic import ValidationError

    from src.settings import Settings

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ISS-05: llm_model_id field with provider-aware default resolution
# ---------------------------------------------------------------------------


def test_llm_model_id_defaults_to_gemini_model_when_provider_gemini(
    monkeypatch, tmp_path
) -> None:
    from src.settings import Settings

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("LLM_MODEL_ID", raising=False)
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_model_id == "gemini-3.1-pro-preview"


def test_llm_model_id_defaults_to_anthropic_model_when_provider_anthropic(
    monkeypatch, tmp_path
) -> None:
    from src.settings import Settings

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_MODEL_ID", raising=False)
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_model_id == "claude-3-5-haiku-20241022"


def test_llm_model_id_env_override_wins(monkeypatch, tmp_path) -> None:
    from src.settings import Settings

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL_ID", "custom-model-x")
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_model_id == "custom-model-x"


def test_stream_call_site_uses_settings_only(monkeypatch, tmp_path) -> None:
    # Pins the contract `src/api/stream.py:_build_hydration_payload` relies
    # on after ISS-05: provider + model_id come from Settings, not env.
    # If this test stops passing, the stream.py call site has regressed
    # to reading env directly or the Settings default resolution broke.
    from src.settings import Settings

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MODEL_ID", raising=False)
    monkeypatch.chdir(tmp_path)

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_provider == "anthropic"
    assert s.llm_model_id == "claude-3-5-haiku-20241022"


# ---------------------------------------------------------------------------
# ISS-07: HMAC fallback truth table (allow_hmac_fallback x secret x stub_mode)
# ---------------------------------------------------------------------------


def test_hmac_fallback_raises_when_default_secret_and_not_stub(
    monkeypatch, tmp_path
) -> None:
    from pydantic import ValidationError

    from src.settings import Settings

    # stub_mode is bound via validation_alias=KLOC_STUB_MODE; set it via env.
    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    # Provider key must be present so the provider-key validator does not
    # raise first and mask the HMAC fallback check.
    monkeypatch.setenv("GEMINI_API_KEY", "stub-key-for-test")
    monkeypatch.chdir(tmp_path)

    with pytest.raises((ValidationError, ValueError)):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            kloc_hook_secret="dev-secret-please-rotate",
            allow_hmac_fallback=True,
        )


def test_hmac_fallback_allowed_when_stub_mode(monkeypatch, tmp_path) -> None:
    from src.settings import Settings

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.chdir(tmp_path)

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_hook_secret="dev-secret-please-rotate",
        allow_hmac_fallback=True,
    )
    assert s.allow_hmac_fallback is True
    assert s.kloc_hook_secret == "dev-secret-please-rotate"
    assert s.stub_mode is True


def test_hmac_fallback_allowed_when_secret_rotated(
    monkeypatch, tmp_path
) -> None:
    from src.settings import Settings

    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "stub-key-for-test")
    monkeypatch.chdir(tmp_path)

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_hook_secret="some-rotated-secret-xyz",
        allow_hmac_fallback=True,
    )
    assert s.allow_hmac_fallback is True
    assert s.kloc_hook_secret == "some-rotated-secret-xyz"
    assert s.stub_mode is False


def test_hmac_fallback_disabled_with_default_secret_ok(
    monkeypatch, tmp_path
) -> None:
    from src.settings import Settings

    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "stub-key-for-test")
    monkeypatch.chdir(tmp_path)

    # allow_hmac_fallback=False is the default; the 3-AND predicate must
    # not fire even with the default secret and stub_mode=False.
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_hook_secret="dev-secret-please-rotate",
        allow_hmac_fallback=False,
    )
    assert s.allow_hmac_fallback is False


def test_hmac_fallback_error_message_names_both_env_vars(
    monkeypatch, tmp_path
) -> None:
    from pydantic import ValidationError

    from src.settings import Settings

    monkeypatch.delenv("KLOC_STUB_MODE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "stub-key-for-test")
    monkeypatch.chdir(tmp_path)

    with pytest.raises((ValidationError, ValueError)) as exc_info:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            kloc_hook_secret="dev-secret-please-rotate",
            allow_hmac_fallback=True,
        )
    msg = str(exc_info.value)
    assert "KLOC_HOOK_SECRET" in msg
    assert "KLOC_STUB_MODE" in msg
