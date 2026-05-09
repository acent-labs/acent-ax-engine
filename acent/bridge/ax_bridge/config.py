"""Bridge runtime settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # HTTP server
    port: int = 8001
    log_level: str = "INFO"

    # Auth — shared secret with acent-flow
    ax_engine_internal_token: str = Field(default="dev-token")

    # Hermes invocation
    # The command + args used to spawn the Hermes ACP subprocess for each
    # request. Defaults to launching the user-installed Hermes via the
    # ``hermes`` CLI; override in production with the in-container Python
    # binary running ``-m acp_adapter.entry``.
    hermes_command: str = "hermes"
    hermes_args: tuple[str, ...] = ("acp",)
    hermes_cwd: str | None = None
    hermes_env_passthrough: tuple[str, ...] = (
        "PATH", "HOME", "HERMES_HOME",
        "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
    )

    # Skill / prompt
    acent_skill_name: str = "acent-ax-analysis"

    # Per-request lifecycle
    spawn_timeout_s: float = 10.0
    prompt_timeout_s: float = 120.0


_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached
