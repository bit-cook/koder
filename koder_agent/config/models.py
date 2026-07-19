"""Pydantic models for Koder configuration."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ModelConfig(BaseModel):
    """Model configuration settings.

    The provider field accepts any LiteLLM-supported provider name.
    See https://docs.litellm.ai/docs/providers for the full list.

    Common providers: openai, anthropic, google, azure, vertex_ai,
    bedrock, cohere, replicate, ollama, huggingface, etc.
    """

    # Core settings
    name: str = Field(default="gpt-4.1", description="Model name")
    provider: str = Field(
        default="openai",
        description="Model provider (any LiteLLM-supported provider)",
    )
    api_key: Optional[str] = Field(default=None, description="API key for the provider")
    base_url: Optional[str] = Field(default=None, description="Custom base URL")
    context_window: Optional[int] = Field(
        default=None,
        gt=0,
        description=(
            "Authoritative context window override for custom/unknown main models. "
            "Env: KODER_CONTEXT_WINDOW"
        ),
    )

    # Small model for auxiliary calls (title generation, compaction, etc.)
    small_model: Optional[str] = Field(
        default=None,
        description="Small/cheap model for auxiliary LLM calls (title gen, compaction). Env: KODER_SMALL_MODEL",
    )
    small_model_context_window: Optional[int] = Field(
        default=None,
        gt=0,
        description=(
            "Authoritative context window override for the auxiliary small model. "
            "Env: KODER_SMALL_MODEL_CONTEXT_WINDOW"
        ),
    )

    # Azure-specific settings
    azure_api_version: Optional[str] = Field(
        default=None, description="Azure API version (e.g., 2025-04-01-preview)"
    )

    # Vertex AI-specific settings
    vertex_ai_location: Optional[str] = Field(
        default=None, description="Vertex AI region (e.g., us-central1)"
    )
    vertex_ai_credentials_path: Optional[str] = Field(
        default=None, description="Path to Vertex AI service account credentials JSON"
    )

    # Reasoning effort setting
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = Field(
        default="medium",
        description=(
            "Reasoning effort for reasoning models "
            "(none, minimal, low, medium, high, xhigh, or max)"
        ),
    )

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _default_reasoning_effort(cls, value):
        """Treat an explicit null the same as an omitted effort setting."""
        return "medium" if value is None else value


class CLIConfig(BaseModel):
    """CLI default settings."""

    session: Optional[str] = Field(default=None, description="Default session name")
    stream: bool = Field(default=True, description="Enable streaming by default")


class MCPServerConfigYaml(BaseModel):
    """MCP server configuration for YAML storage."""

    name: str = Field(..., description="Unique name for the server")
    transport_type: str = Field(default="stdio", description="Transport type (stdio, sse, http)")
    command: Optional[str] = Field(default=None, description="Command for stdio servers")
    args: Optional[List[str]] = Field(default_factory=list, description="Arguments for command")
    env_vars: Optional[Dict[str, str]] = Field(
        default_factory=dict, description="Environment variables"
    )
    url: Optional[str] = Field(default=None, description="URL for SSE/HTTP servers")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict, description="HTTP headers")
    cache_tools_list: bool = Field(default=False, description="Cache tools list")
    allowed_tools: Optional[List[str]] = Field(default=None, description="Allowed tools whitelist")
    blocked_tools: Optional[List[str]] = Field(default=None, description="Blocked tools blacklist")


class MCPLocalProjectConfigYaml(BaseModel):
    """Per-project private MCP servers stored in the user config."""

    project_root: str = Field(..., description="Absolute project root for local-scope MCP servers")
    servers: List[MCPServerConfigYaml] = Field(default_factory=list)


class SkillsConfig(BaseModel):
    """Skills configuration settings for progressive disclosure."""

    enabled: bool = Field(default=True, description="Enable skills feature")
    project_skills_dir: str = Field(
        default=".koder/skills", description="Project-level skills directory"
    )
    user_skills_dir: str = Field(
        default="~/.koder/skills", description="User-level skills directory"
    )


class VoiceConfig(BaseModel):
    """Voice dictation configuration."""

    enabled: bool = Field(default=False, description="Enable interactive voice dictation")
    provider: Optional[str] = Field(
        default=None,
        description="Voice provider override (openai, chatgpt, google, gemini)",
    )
    model: Optional[str] = Field(default=None, description="Voice transcription model override")
    api_key: Optional[str] = Field(default=None, description="Voice provider API key override")
    base_url: Optional[str] = Field(default=None, description="Voice provider base URL override")
    api_version: Optional[str] = Field(
        default=None, description="Voice provider API version override"
    )


class KoderConfig(BaseModel):
    """Root configuration model for Koder CLI."""

    model: ModelConfig = Field(default_factory=ModelConfig)
    cli: CLIConfig = Field(default_factory=CLIConfig)
    mcp_servers: List[MCPServerConfigYaml] = Field(default_factory=list)
    mcp_local_projects: List[MCPLocalProjectConfigYaml] = Field(default_factory=list)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
