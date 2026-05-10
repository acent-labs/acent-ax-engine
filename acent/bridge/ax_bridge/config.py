"""Bridge runtime settings."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    """Settings for the AX bridge.

    Resolved from process environment. The only mandatory value is
    `AX_ENGINE_INTERNAL_TOKEN`, the bearer the FastAPI gateway sends.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    ax_engine_internal_token: str = Field(
        ...,
        description="Bearer expected on POST /v1/analyze",
    )
    hermes_command: list[str] = Field(
        default_factory=lambda: ["hermes", "acp"],
        description="Argv used to spawn one Hermes ACP subprocess per request",
    )
    hermes_skill: str = Field(
        default="acent-ax-analysis",
        description="Skill the bridge invokes once the ACP session is initialized",
    )
    hermes_cwd: str | None = Field(
        default=None,
        description="Working directory for the spawned Hermes process (None → inherit)",
    )
    prompt_timeout_s: float = Field(
        default=120.0,
        gt=0.0,
        description="Hard ceiling for a single analysis prompt before HERMES_TIMEOUT",
    )
