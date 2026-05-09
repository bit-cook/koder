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
from koder_agent.harness.sandbox.backend import SandboxBackendStatus
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.sandbox.registry import get_backend_statuses, normalize_backend_id


@dataclass(frozen=True)
class SandboxSettingsState:
    """Resolved sandbox configuration for the current working directory."""

    enabled: bool
    auto_allow_bash_if_sandboxed: bool
    excluded_commands: tuple[str, ...]
    enabled_platforms: tuple[str, ...] | None
    settings_path: Path
    policy_locked: bool
    platform: str
    platform_supported: bool
    platform_enabled: bool
    dependency_errors: tuple[str, ...]
    policy_mode: str = "danger-full-access"
    backend: str = "unix-local"
    backend_available: bool = False
    backend_reason: str = "not checked"
    network_access: bool = False
    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    allow_read: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    allow_write: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    policy: SandboxPolicy | None = None
    backend_statuses: tuple[SandboxBackendStatus, ...] = ()


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


def _resolve_sandbox_dict(cwd: str | Path) -> dict[str, Any]:
    keys = (
        "enabled",
        "mode",
        "backend",
        "networkAccess",
        "allowedDomains",
        "deniedDomains",
        "writableRoots",
        "allowRead",
        "denyRead",
        "allowWrite",
        "denyWrite",
        "protectedPaths",
        "autoAllowBashIfSandboxed",
        "enabledPlatforms",
    )
    resolved: dict[str, Any] = {}
    for key in keys:
        marker = object()
        value = _resolve_sandbox_value(cwd, key, marker)
        if value is not marker:
            resolved[key] = value
    excluded = _collect_excluded_commands(cwd)
    if excluded:
        resolved["excludedCommands"] = list(excluded)
    return resolved


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
    raw_sandbox = _resolve_sandbox_dict(cwd)
    policy = SandboxPolicy.from_config(raw_sandbox)
    platform = _detect_platform()
    enabled_platforms = policy.enabled_platforms
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
                "mode",
                "backend",
                "networkAccess",
                "writableRoots",
                "allowRead",
                "denyRead",
                "allowWrite",
                "denyWrite",
                "protectedPaths",
                "autoAllowBashIfSandboxed",
            )
        )

    backend = normalize_backend_id(policy.backend)
    backend_statuses = tuple(get_backend_statuses(backend))
    backend_status = next(
        (status for status in backend_statuses if status.backend_id == backend),
        None,
    )
    backend_available = bool(backend_status and backend_status.available)
    backend_reason = backend_status.reason if backend_status else "unknown backend"

    dependency_errors = _check_dependencies(platform)
    if backend_status and backend_status.dependency_errors:
        dependency_errors = (*dependency_errors, *backend_status.dependency_errors)

    return SandboxSettingsState(
        enabled=policy.enabled,
        auto_allow_bash_if_sandboxed=policy.auto_allow_bash_if_sandboxed,
        excluded_commands=policy.excluded_commands,
        enabled_platforms=enabled_platforms,
        settings_path=_local_settings_path(cwd),
        policy_locked=policy_locked,
        platform=platform,
        platform_supported=platform_supported,
        platform_enabled=platform_enabled,
        dependency_errors=dependency_errors,
        policy_mode=policy.mode,
        backend=backend,
        backend_available=backend_available,
        backend_reason=backend_reason,
        network_access=policy.network_access,
        allowed_domains=policy.allowed_domains,
        denied_domains=policy.denied_domains,
        writable_roots=policy.writable_roots,
        allow_read=policy.allow_read,
        deny_read=policy.deny_read,
        allow_write=policy.allow_write,
        deny_write=policy.deny_write,
        protected_paths=policy.protected_paths,
        policy=policy,
        backend_statuses=backend_statuses,
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
    backend: str | None = None,
    auto_allow_bash_if_sandboxed: bool | None = None,
) -> Path:
    target = _local_settings_path(cwd)
    loaded = _load_json(target) if target.exists() else {}
    sandbox = loaded.get("sandbox")
    if not isinstance(sandbox, dict):
        sandbox = {}
    if enabled is not None:
        sandbox["enabled"] = enabled
        if enabled is False:
            sandbox["mode"] = "danger-full-access"
        else:
            sandbox["mode"] = "workspace-write"
    if backend is not None:
        sandbox["backend"] = normalize_backend_id(backend)
    if auto_allow_bash_if_sandboxed is not None:
        sandbox["autoAllowBashIfSandboxed"] = auto_allow_bash_if_sandboxed
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
