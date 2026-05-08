"""Runtime config schema layered on top of the existing Koder config."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from koder_agent.config.models import KoderConfig
from koder_agent.harness.reasoning_display import (
    ReasoningDisplayMode,
    normalize_reasoning_display_mode,
)


class HarnessCompanionConfig(BaseModel):
    """Persisted companion soul for the local /buddy runtime."""

    name: str
    personality: str
    hatched_at: int


class HarnessRuntimeConfig(BaseModel):
    """Runtime-owned harness settings."""

    interactive_shell: str = Field(default="runtime")
    permission_mode: str = Field(default="default")
    teammate_mode: Literal["auto", "tmux", "in-process"] = Field(default="auto")
    last_release_notes_seen: str | None = Field(default=None)
    advisor_model: str | None = Field(default=None)
    brief_mode_enabled: bool = Field(default=False)
    companion: HarnessCompanionConfig | None = Field(default=None)
    companion_muted: bool = Field(default=False)
    reasoning_display: ReasoningDisplayMode = Field(default="off")

    @field_validator("reasoning_display", mode="before")
    @classmethod
    def _coerce_reasoning_display(cls, value: object) -> ReasoningDisplayMode:
        return normalize_reasoning_display_mode(value)


class RuntimeConfig(KoderConfig):
    """Extended runtime config stored at the existing config path."""

    harness: HarnessRuntimeConfig = Field(default_factory=HarnessRuntimeConfig)
