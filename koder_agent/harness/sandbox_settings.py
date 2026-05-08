"""Helpers for local sandbox policy settings and status."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from koder_agent.harness.managed_settings import load_managed_settings, managed_settings_path
from koder_agent.harness.paths import harness_home_dir, settings_path
from koder_agent.harness.permissions.rules import match_permission_rule, parse_permission_rule


@dataclass(frozen=True)
class SandboxSettingsState:
    """Resolved sandbox configuration for the current working directory."""

    enabled: bool
    auto_allow_bash_if_sandboxed: bool
    allow_unsandboxed_commands: bool
    fail_if_unavailable: bool
    excluded_commands: tuple[str, ...]
    enabled_platforms: tuple[str, ...] | None
    settings_path: Path
    policy_locked: bool
    platform: str
    platform_supported: bool
    platform_enabled: bool
    dependency_errors: tuple[str, ...]
    execution_mode: str = "local-policy-only"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _iter_project_settings_paths(cwd: str | Path) -> list[Path]:
    paths: list[Path] = []
    current = Path(cwd).resolve()
    home = Path.home().resolve()
    while True:
        local = current / ".koder" / "settings.local.json"
        if local.exists():
            paths.append(local)
        project = settings_path(current)
        if project.exists():
            paths.append(project)
        if current == home or current.parent == current:
            break
        if (current / ".git").exists():
            break
        current = current.parent
    return paths


def _iter_effective_sources(cwd: str | Path) -> list[Path]:
    managed = managed_settings_path()
    sources: list[Path] = []
    if managed.exists():
        sources.append(managed)
    sources.extend(_iter_project_settings_paths(cwd))
    user = harness_home_dir() / "settings.json"
    if user.exists():
        sources.append(user)
    return sources


def _resolve_sandbox_value(cwd: str | Path, key: str, default: Any) -> Any:
    for path in _iter_effective_sources(cwd):
        sandbox = _load_json(path).get("sandbox")
        if isinstance(sandbox, dict) and key in sandbox:
            return sandbox.get(key)
    return default


def _collect_excluded_commands(cwd: str | Path) -> tuple[str, ...]:
    seen: set[str] = set()
    collected: list[str] = []
    for path in _iter_effective_sources(cwd):
        sandbox = _load_json(path).get("sandbox")
        values = sandbox.get("excludedCommands") if isinstance(sandbox, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            collected.append(normalized)
    return tuple(collected)


def _local_settings_path(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / ".koder" / "settings.local.json"


def _detect_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
            return "wsl"
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def _is_supported_platform(platform: str) -> bool:
    if platform in {"macos", "linux"}:
        return True
    if platform == "wsl":
        return "WSL_INTEROP" in os.environ
    return False


def _check_dependencies(platform: str) -> tuple[str, ...]:
    if platform not in {"linux", "wsl"}:
        return ()
    missing: list[str] = []
    if shutil.which("bwrap") is None:
        missing.append("bubblewrap")
    if shutil.which("socat") is None:
        missing.append("socat")
    return tuple(missing)


def _normalize_enabled_platforms(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    normalized = [str(item).strip().lower() for item in value if str(item).strip()]
    return tuple(normalized)


def resolve_sandbox_settings(cwd: str | Path) -> SandboxSettingsState:
    platform = _detect_platform()
    enabled_platforms = _normalize_enabled_platforms(
        _resolve_sandbox_value(cwd, "enabledPlatforms", None)
    )
    platform_supported = _is_supported_platform(platform)
    platform_enabled = enabled_platforms is None or platform in enabled_platforms
    managed = load_managed_settings()
    managed_sandbox = managed.data.get("sandbox") if managed.valid else None
    policy_locked = False
    if isinstance(managed_sandbox, dict):
        policy_locked = any(
            key in managed_sandbox
            for key in (
                "enabled",
                "autoAllowBashIfSandboxed",
                "allowUnsandboxedCommands",
            )
        )

    return SandboxSettingsState(
        enabled=bool(_resolve_sandbox_value(cwd, "enabled", False)),
        auto_allow_bash_if_sandboxed=bool(
            _resolve_sandbox_value(cwd, "autoAllowBashIfSandboxed", True)
        ),
        allow_unsandboxed_commands=bool(
            _resolve_sandbox_value(cwd, "allowUnsandboxedCommands", True)
        ),
        fail_if_unavailable=bool(_resolve_sandbox_value(cwd, "failIfUnavailable", False)),
        excluded_commands=_collect_excluded_commands(cwd),
        enabled_platforms=enabled_platforms,
        settings_path=_local_settings_path(cwd),
        policy_locked=policy_locked,
        platform=platform,
        platform_supported=platform_supported,
        platform_enabled=platform_enabled,
        dependency_errors=_check_dependencies(platform),
    )


def is_excluded_command(command: str, *, cwd: str | Path) -> bool:
    for pattern in resolve_sandbox_settings(cwd).excluded_commands:
        if match_permission_rule(parse_permission_rule(pattern), command):
            return True
    return False


def update_local_sandbox_settings(
    cwd: str | Path,
    *,
    enabled: bool | None = None,
    auto_allow_bash_if_sandboxed: bool | None = None,
    allow_unsandboxed_commands: bool | None = None,
) -> Path:
    target = _local_settings_path(cwd)
    loaded = _load_json(target) if target.exists() else {}
    sandbox = loaded.get("sandbox")
    if not isinstance(sandbox, dict):
        sandbox = {}
    if enabled is not None:
        sandbox["enabled"] = enabled
    if auto_allow_bash_if_sandboxed is not None:
        sandbox["autoAllowBashIfSandboxed"] = auto_allow_bash_if_sandboxed
    if allow_unsandboxed_commands is not None:
        sandbox["allowUnsandboxedCommands"] = allow_unsandboxed_commands
    loaded["sandbox"] = sandbox
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def add_excluded_command(cwd: str | Path, command_pattern: str) -> tuple[Path, str]:
    target = _local_settings_path(cwd)
    loaded = _load_json(target) if target.exists() else {}
    sandbox = loaded.get("sandbox")
    if not isinstance(sandbox, dict):
        sandbox = {}
    existing = sandbox.get("excludedCommands")
    values = [str(value).strip() for value in existing] if isinstance(existing, list) else []
    normalized = command_pattern.strip()
    if normalized and normalized not in values:
        values.append(normalized)
    sandbox["excludedCommands"] = values
    loaded["sandbox"] = sandbox
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target, normalized
