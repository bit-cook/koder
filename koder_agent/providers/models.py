"""Models used by the provider compatibility layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ProviderAuthSnapshot:
    """Snapshot of provider auth information available to the runtime."""

    provider: str
    api_key: Optional[str]
    headers: dict[str, str] = field(default_factory=dict)
    is_oauth: bool = False


@dataclass(frozen=True)
class ResolvedModelClient:
    """Resolved client settings for the currently selected model."""

    model_name: str
    api_key: Optional[str]
    base_url: Optional[str]
    litellm_kwargs: dict[str, Any]
    native_openai: bool
