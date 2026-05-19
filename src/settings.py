"""Application settings.

Single `Settings(BaseSettings)` class reading the env vars declared in
`.env.example`. Validation happens at boot so misconfiguration surfaces
at startup rather than on first request.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


LlmProvider = Literal["gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent",
        description="SQLAlchemy async URL (asyncpg driver).",
    )

    minio_endpoint_url: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_use_ssl: bool = False
    artifact_bucket: str = "kloc-agent-artifacts-dev"

    stub_mode: bool = Field(
        default=False,
        validation_alias="KLOC_STUB_MODE",
        description=(
            "When true, skip boot-time provider-key validation. Tests run "
            "with this true; production with this false."
        ),
    )

    runner_warm_idle_s: int = 60
    runner_heartbeat_timeout_s: int = 30
    runner_image_tag: str = "kloc-agent-runner:dev"

    # Default matches the explicit `networks.kloc.name: kloc` block in
    # docker-compose.yml so the runner joins the same bridge as the
    # backend regardless of compose project name
    # (COMPOSE_PROJECT_NAME doesn't apply to explicit-name networks).
    kloc_docker_network: str = "kloc"
    kloc_skills_dir_host: str = "./skills"

    llm_provider: LlmProvider = "gemini"
    gemini_api_key: str | None = None

    # Empty string sentinel is replaced with `"gemini-3.1-pro-preview"`
    # inside `_validate_provider_key`, so consumers can rely on
    # `settings.llm_model_id` being a non-empty `str` after construction.
    llm_model_id: str = Field(
        default="",
        description=(
            "Concrete model identifier passed into HydrationPayload. "
            "Defaults to `gemini-3.1-pro-preview` when unset."
        ),
    )

    backend_url: str = "http://localhost:8000"

    kloc_mcp_url: str = Field(
        default="http://host.docker.internal:8765/mcp",
        description=(
            "Streamable-HTTP MCP URL kloc-agent runners use to reach "
            "kloc-intelligence on the operator host. kloc-intelligence "
            "runs in its own docker-compose stack (Neo4j + Qdrant + "
            "`kloc-intelligence mcp-server-http`); the runner connects "
            "over Docker's host-gateway alias. Backend just plumbs this "
            "value into the HydrationPayload — no Neo4j/Qdrant/sot.json "
            "knowledge in kloc-agent."
        ),
    )

    kloc_hook_secret: str = Field(
        default="dev-secret-please-rotate",
        description="HMAC bootstrap secret. Per-runner secrets are minted at "
        "spawn; this is only the boot-time fallback.",
    )

    kloc_deny_tools: str = Field(
        default="",
        description="Comma-separated tool names to deny on the webhook "
        "policy decision. Empty (default) means allow-all.",
    )

    @property
    def deny_tools_set(self) -> set[str]:
        return {t.strip() for t in self.kloc_deny_tools.split(",") if t.strip()}

    # `NoDecode` opts out of pydantic-settings' built-in JSON decoding for
    # complex types. Without it, a raw string env value like
    # `http://localhost:3000` would fail `json.loads` before the
    # `_split_cors_allow_origins` validator below ever ran.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        validation_alias="KLOC_CORS_ALLOW_ORIGINS",
        description=(
            "Origins allowed for browser CORS requests against the FastAPI "
            "backend. Accepts a comma-separated string from env "
            "(`KLOC_CORS_ALLOW_ORIGINS=http://a,http://b`) or a Python list "
            "in code/tests. Used by `fastapi.middleware.cors.CORSMiddleware` "
            "installed in `src/main.create_app`."
        ),
    )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_cors_allow_origins(cls, v: object) -> object:
        # Pydantic-settings hands us a raw string when the env var is set;
        # split on comma and strip empties so `KLOC_CORS_ALLOW_ORIGINS=
        # "http://localhost:3000, http://localhost:3001"` resolves to a
        # two-element list. A list (default / in-code construction) flows
        # through unchanged.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    diag_events: bool = Field(
        default=False,
        validation_alias="KLOC_DIAG_EVENTS",
        description=(
            "When true, the per-frame stderr diagnostic emitters in the "
            "JSONL ingress and HMAC webhook receiver are activated. Off "
            "by default so production traffic does not pay a sync-stderr "
            "write per AG-UI event."
        ),
    )

    allow_hmac_fallback: bool = Field(
        default=False,
        description=(
            "When True, the HMAC webhook receiver accepts the bootstrap "
            "secret (`kloc_hook_secret`) for runner_ids that have no entry "
            "in the runner registry. Intended for unit tests / stub mode. "
            "Default False (strict): unknown runner_ids fail with 401 even "
            "if the body is signed with the global bootstrap secret. "
            "Read from env var `ALLOW_HMAC_FALLBACK` (case-insensitive)."
        ),
    )

    @model_validator(mode="after")
    def _validate_provider_key(self) -> "Settings":
        # Validate at boot, not first LLM call. Stub mode (tests / CI)
        # skips so suites can construct Settings without a real key.
        if not self.stub_mode and not self.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY required (or set KLOC_STUB_MODE=true)"
            )

        # Opting into HMAC fallback while still using the placeholder
        # bootstrap secret means any caller who learned the well-known string
        # `"dev-secret-please-rotate"` can forge runner webhooks. Refuse boot
        # unless the operator either rotated the secret or explicitly flagged
        # this as a stub / test run.
        if (
            self.allow_hmac_fallback
            and self.kloc_hook_secret == "dev-secret-please-rotate"
            and not self.stub_mode
        ):
            raise ValueError(
                "allow_hmac_fallback=True with the default kloc_hook_secret "
                "is unsafe: any caller who knows the placeholder bootstrap "
                "secret can forge runner webhooks. Set KLOC_HOOK_SECRET to a "
                "non-default value, or set KLOC_STUB_MODE=true for test runs."
            )

        if not self.llm_model_id:
            object.__setattr__(self, "llm_model_id", "gemini-3.1-pro-preview")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
