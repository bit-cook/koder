"""Sandbox policy model and config parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]

DEFAULT_PROTECTED_PATHS = (".git", ".koder", ".agents", ".codex")
DEFAULT_SENSITIVE_DENY_WRITE = (
    ".env",
    ".env.*",
    ".mcp.json",
    ".gitconfig",
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".zprofile",
)
VALID_MODES = {"read-only", "workspace-write", "danger-full-access"}
DEFAULT_BACKEND = "unix-local"
BACKEND_ALIASES = {
    "local": "unix-local",
    "sdk-unix-local": "unix-local",
    "unix-local": "unix-local",
    "sdk-docker": "docker",
    "docker": "docker",
    "sdk-cloudflare": "cloudflare",
    "cloudflare": "cloudflare",
    "sdk-e2b": "e2b",
    "e2b": "e2b",
    "sdk-modal": "modal",
    "modal": "modal",
    "sdk-vercel": "vercel",
    "vercel": "vercel",
}


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return tuple(items)


def _normalize_mode(value: Any, *, enabled: bool) -> SandboxMode:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in VALID_MODES:
            return normalized  # type: ignore[return-value]
    return "workspace-write" if enabled else "danger-full-access"


def _normalize_backend(value: Any) -> str:
    normalized = str(value or DEFAULT_BACKEND).strip().lower()
    if not normalized or normalized == "auto":
        return DEFAULT_BACKEND
    return BACKEND_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class SandboxPolicy:
    """Merged Codex/Claude sandbox policy resolved for a workspace."""

    mode: SandboxMode = "danger-full-access"
    backend: str = DEFAULT_BACKEND
    network_access: bool = False
    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    allow_read: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    allow_write: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = DEFAULT_SENSITIVE_DENY_WRITE
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    auto_allow_bash_if_sandboxed: bool = True
    excluded_commands: tuple[str, ...] = ()
    enabled_platforms: tuple[str, ...] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.mode != "danger-full-access"

    @classmethod
    def from_config(cls, sandbox: dict[str, Any] | None) -> "SandboxPolicy":
        data = sandbox if isinstance(sandbox, dict) else {}
        enabled = _bool_value(data.get("enabled"), False)
        mode = _normalize_mode(data.get("mode"), enabled=enabled)
        if mode == "danger-full-access":
            enabled = False

        protected = _string_tuple(data.get("protectedPaths")) or DEFAULT_PROTECTED_PATHS
        deny_write = _string_tuple(data.get("denyWrite")) or DEFAULT_SENSITIVE_DENY_WRITE
        return cls(
            mode=mode,
            backend=_normalize_backend(data.get("backend")),
            network_access=_bool_value(data.get("networkAccess"), False),
            allowed_domains=_string_tuple(data.get("allowedDomains")),
            denied_domains=_string_tuple(data.get("deniedDomains")),
            writable_roots=_string_tuple(data.get("writableRoots")),
            allow_read=_string_tuple(data.get("allowRead")),
            deny_read=_string_tuple(data.get("denyRead")),
            allow_write=_string_tuple(data.get("allowWrite")),
            deny_write=deny_write,
            protected_paths=protected,
            auto_allow_bash_if_sandboxed=_bool_value(data.get("autoAllowBashIfSandboxed"), True),
            excluded_commands=_string_tuple(data.get("excludedCommands")),
            enabled_platforms=_string_tuple(data.get("enabledPlatforms")) or None,
            raw=dict(data),
        )

    def protected_path_roots(self, repo_root: Path) -> tuple[Path, ...]:
        roots: list[Path] = []
        for value in (*self.protected_paths, *self.deny_write):
            if "*" in value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = repo_root / path
            roots.append(path.resolve(strict=False))
        return tuple(roots)
