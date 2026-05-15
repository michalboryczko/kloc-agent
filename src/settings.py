"""Application settings (Phase 1.A2).

Single `Settings(BaseSettings)` class reading the env vars declared in
`.env.example`. Validation happens on boot so misconfiguration surfaces
at startup rather than first request.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LlmProvider = Literal["anthropic", "openrouter", "bedrock", "gemini"]


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

    runner_warm_idle_s: int = 60
    runner_heartbeat_timeout_s: int = 30
    runner_image_tag: str = "kloc-agent-runner:dev"

    # Runner spawn config (dev-2 CR). Default matches the explicit
    # `networks.kloc.name: kloc` block in docker-compose.yml so the runner
    # joins the same bridge as the backend regardless of compose project
    # name (COMPOSE_PROJECT_NAME doesn't apply to explicit-name networks).
    kloc_docker_network: str = "kloc"
    kloc_skills_dir_host: str = "./skills"
    kloc_runner_mode: Literal["docker", "stub"] = Field(
        default="docker",
        description=(
            "B-INFRA-1: 'docker' (default) requires aiodocker + a bind-mounted "
            "/var/run/docker.sock — DockerRunner construction failure hard-fails "
            "boot. 'stub' is the CI / local-without-docker mode and tolerates "
            "missing aiodocker."
        ),
    )

    llm_provider: LlmProvider = "gemini"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

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
        description="Comma-separated tool names to deny for AC19/QA10. "
        "Empty (default) means allow-all (PoC behaviour).",
    )

    @property
    def deny_tools_set(self) -> set[str]:
        return {t.strip() for t in self.kloc_deny_tools.split(",") if t.strip()}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
