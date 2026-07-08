"""Sandbox policy model and config parsing."""

from __future__ import annotations

import fnmatch
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]

# Backends able to actually enforce a network deny/allow policy at runtime. The
# unix-local backend cannot, so network_access must never be presented as
# enforced when it is the resolved backend (honesty-first, finding #4).
# NOTE: Docker is excluded because the SDK docker backend does NOT set
# --network=none by default; containers use the default bridge network.
# Only cloud-hosted backends guarantee network isolation.
NETWORK_ENFORCING_BACKENDS = frozenset({"cloudflare", "e2b", "modal", "vercel"})

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
        # An invalid/misspelled mode while the sandbox is enabled must never
        # silently fall through to danger-full-access (finding #6): fail closed
        # to the safer workspace-write and surface a warning so the misconfig is
        # not hidden.
        if enabled and normalized:
            warnings.warn(
                f"invalid sandbox mode {value!r}; falling back to workspace-write",
                RuntimeWarning,
                stacklevel=2,
            )
            return "workspace-write"
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

    @property
    def network_enforced(self) -> bool:
        """Whether the resolved backend can actually enforce the network policy.

        The unix-local backend applies no network confinement, so a policy that
        denies network access is *not* enforced there. Callers/status output must
        rely on this flag rather than assuming ``network_access`` is honored
        (honesty-first, finding #4).
        """
        return self.backend in NETWORK_ENFORCING_BACKENDS

    @property
    def network_restricted_but_unenforced(self) -> bool:
        """True when the policy asks to restrict network but the backend can't.

        Signals the dishonest-confinement case: network_access is disabled (or
        domain lists are set) yet the selected backend cannot enforce it.
        """
        wants_restriction = (
            not self.network_access or bool(self.allowed_domains) or bool(self.denied_domains)
        )
        return wants_restriction and not self.network_enforced

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
                # Glob patterns are handled by deny_write_globs / fnmatch; they
                # cannot be resolved to concrete literal roots here (finding #3).
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = repo_root / path
            roots.append(path.resolve(strict=False))
        return tuple(roots)

    def deny_write_globs(self) -> tuple[str, ...]:
        """Return the glob-style deny_write patterns (e.g. ``.env.*``).

        These are intentionally excluded from ``protected_path_roots`` (which only
        yields literal roots) so they were previously dropped entirely (finding
        #3). Callers match write targets against them with :meth:`matches_deny_write_glob`.
        """
        return tuple(value for value in (*self.protected_paths, *self.deny_write) if "*" in value)

    def matches_deny_write_glob(self, target: str) -> str | None:
        """Return the matched glob pattern if ``target`` hits a deny_write glob.

        ``target`` may be a full/relative path; both its basename and the path as
        given are tested so patterns like ``.env.*`` match ``.env.local`` and
        ``config/.env.production`` alike.
        """
        basename = Path(target).name
        for pattern in self.deny_write_globs():
            if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(target, pattern):
                return pattern
        return None
