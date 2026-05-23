"""Onboarding wizard state management for Koder setup."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OnboardingState:
    """Represents the current state of user onboarding."""

    completed: bool = False
    api_key_configured: bool = False
    model_selected: bool = False
    workspace_trusted: bool = False
    auth_provider_hint: str | None = None


def _is_github_copilot_configured(runtime_env: Mapping[str, str | None]) -> bool:
    model = (runtime_env.get("KODER_MODEL") or "").strip().lower()
    if model.startswith("litellm/"):
        model = model[len("litellm/") :]
    if model.startswith("github_copilot/"):
        return True

    try:
        from koder_agent.config import get_config

        config = get_config()
        provider = (config.model.provider or "").strip().lower()
        name = (config.model.name or "").strip().lower()
        return provider == "github_copilot" or name.startswith("github_copilot/")
    except Exception:
        return False


def _has_litellm_copilot_token(runtime_env: Mapping[str, str | None]) -> bool:
    token_dir = runtime_env.get("GITHUB_COPILOT_TOKEN_DIR")
    root = (
        Path(token_dir).expanduser()
        if token_dir
        else Path.home() / ".config/litellm/github_copilot"
    )
    access_token_file = runtime_env.get("GITHUB_COPILOT_ACCESS_TOKEN_FILE") or "access-token"
    api_key_file = runtime_env.get("GITHUB_COPILOT_API_KEY_FILE") or "api-key.json"
    return (root / access_token_file).exists() or (root / api_key_file).exists()


def check_onboarding_state(
    project_dir: Path | None = None, env: Mapping[str, str | None] | None = None
) -> OnboardingState:
    """
    Check the current onboarding state.

    Args:
        project_dir: Project directory to check for workspace trust.
                    Defaults to current working directory.

    Returns:
        OnboardingState with current configuration status.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    runtime_env = os.environ if env is None else env

    # Check for API key in environment variables
    api_key_env_vars = [
        "KODER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ]
    github_copilot_configured = _is_github_copilot_configured(runtime_env)
    api_key_configured = any(runtime_env.get(var) for var in api_key_env_vars)
    auth_provider_hint = "github_copilot" if github_copilot_configured else None
    if github_copilot_configured and _has_litellm_copilot_token(runtime_env):
        api_key_configured = True

    # Check for model configuration (env var OR config file — koder always has a default)
    model_selected = True  # koder has a built-in default model (gpt-4.1)
    if runtime_env.get("KODER_MODEL"):
        model_selected = True  # Explicitly set
    else:
        try:
            from koder_agent.config import get_config

            config = get_config()
            model_selected = bool(config.model.name)
        except Exception:
            pass

    # Check if workspace has .koder directory
    workspace_trusted = (project_dir / ".koder").exists()

    # Onboarding is complete if all checks pass
    completed = api_key_configured and model_selected and workspace_trusted

    return OnboardingState(
        completed=completed,
        api_key_configured=api_key_configured,
        model_selected=model_selected,
        workspace_trusted=workspace_trusted,
        auth_provider_hint=auth_provider_hint,
    )


def get_onboarding_steps(state: OnboardingState) -> list[str]:
    """
    Get list of setup steps needed based on current state.

    Args:
        state: Current onboarding state.

    Returns:
        List of setup step descriptions for incomplete items.
    """
    if state.completed:
        return []

    steps = []

    if not state.api_key_configured and state.auth_provider_hint == "github_copilot":
        steps.append("Authenticate GitHub Copilot: run koder auth login github_copilot")
    elif not state.api_key_configured:
        steps.append(
            "Configure API key: Set KODER_API_KEY, OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY, or another provider's API key"
        )

    if not state.model_selected:
        steps.append(
            "Select model: Set KODER_MODEL environment variable "
            "(e.g., gpt-4o, claude-opus-4-20250514)"
        )

    if not state.workspace_trusted:
        steps.append("Trust workspace: Initialize .koder/ directory in your project")

    return steps


def mark_onboarding_complete(project_dir: Path) -> None:
    """
    Mark onboarding as complete by creating marker file.

    Args:
        project_dir: Project directory where .koder/onboarded will be created.
    """
    koder_dir = project_dir / ".koder"
    koder_dir.mkdir(parents=True, exist_ok=True)

    marker_file = koder_dir / "onboarded"
    marker_file.touch()


def is_onboarding_complete(project_dir: Path) -> bool:
    """
    Check if onboarding has been completed for this project.

    Args:
        project_dir: Project directory to check.

    Returns:
        True if .koder/onboarded marker file exists, False otherwise.
    """
    marker_file = project_dir / ".koder" / "onboarded"
    return marker_file.exists()
