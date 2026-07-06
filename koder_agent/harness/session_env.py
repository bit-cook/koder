"""Session-scoped environment variable helpers."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from koder_agent.harness.paths import harness_home_dir

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def session_env_file(session_id: str) -> Path:
    """Return the persisted session env file path."""
    path = harness_home_dir() / "session-env" / f"{session_id}.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def is_valid_env_name(name: str) -> bool:
    """Return True when the name is a valid shell environment variable key."""
    return bool(ENV_NAME_RE.fullmatch(name))


def load_session_env(session_id: str) -> dict[str, str]:
    """Load session-scoped environment variables from disk."""
    values: dict[str, str] = {}
    for line in (
        session_env_file(session_id).read_text(encoding="utf-8", errors="ignore").splitlines()
    ):
        stripped = line.strip()
        if not stripped.startswith("export "):
            continue
        body = stripped[len("export ") :]
        if "=" not in body:
            continue
        key, raw_value = body.split("=", 1)
        key = key.strip()
        if not is_valid_env_name(key):
            continue
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if parsed else ""
    return values


def write_session_env(session_id: str, values: dict[str, str]) -> None:
    """Persist session-scoped environment variables to disk."""
    lines = [
        f"export {key}={shlex.quote(value)}"
        for key, value in sorted(values.items())
        if is_valid_env_name(key)
    ]
    text = "\n".join(lines)
    if text:
        text += "\n"
    session_env_file(session_id).write_text(text, encoding="utf-8")


def set_session_env_var(session_id: str, name: str, value: str) -> dict[str, str]:
    """Set a session-scoped env var and return the updated mapping."""
    values = load_session_env(session_id)
    values[name] = value
    write_session_env(session_id, values)
    return values


def delete_session_env_var(session_id: str, name: str) -> dict[str, str]:
    """Delete a session-scoped env var and return the updated mapping."""
    values = load_session_env(session_id)
    values.pop(name, None)
    write_session_env(session_id, values)
    return values


def clear_session_env(session_id: str) -> None:
    """Clear all session-scoped environment variables."""
    write_session_env(session_id, {})


def apply_session_env_file_to_process(session_id: str) -> None:
    """Apply session-scoped environment variables to the current process."""
    for key, value in load_session_env(session_id).items():
        os.environ[key] = value


def build_subprocess_env(session_id: str | None = None) -> dict[str, str]:
    """Build child-process environment with optional session-scoped overrides.

    NOTE: This returns the FULL host environment and is intended for the
    non-sandboxed subprocess path, which may legitimately need host credentials
    (e.g. running the user's own tooling). Do NOT pass this straight into a
    sandbox — use :func:`build_sandbox_env` instead (finding #2).
    """
    env = os.environ.copy()
    if session_id:
        env.update(load_session_env(session_id))
    return env


# Env vars safe to forward into the sandbox verbatim. Anything not on this
# allowlist is dropped unless it comes from explicit session-scoped overrides.
SANDBOX_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "PWD",
        "TZ",
        "HOSTNAME",
    }
)

# Case-insensitive substrings/prefixes that mark a variable as a likely secret.
# Any host var matching these is scrubbed from the sandbox env even if it would
# otherwise slip through (defense in depth on top of the allowlist).
_SECRET_NAME_PATTERNS = (
    "_API_KEY",
    "_APIKEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_PASSWD",
    "_CREDENTIAL",
    "_PRIVATE_KEY",
    "_ACCESS_KEY",
    "_SESSION_TOKEN",
)
_SECRET_NAME_PREFIXES = (
    "AWS_",
    "ANTHROPIC_",
    "OPENAI_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "GITHUB_",
    "GH_",
    "KODER_",
    "HF_",
    "HUGGING",
    "COHERE_",
    "MISTRAL_",
    "GROQ_",
    "REPLICATE_",
    "VERCEL_",
    "CLOUDFLARE_",
    "E2B_",
    "MODAL_",
    "SLACK_",
    "STRIPE_",
    "TWILIO_",
    "SENTRY_",
    "NPM_",
    "PYPI_",
    "DOCKER_",
    "DATABASE_",
    "DB_",
)
_SECRET_NAME_EXACT = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NPM_TOKEN",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "APIKEY",
        "API_KEY",
    }
)


def is_probably_secret_env_name(name: str) -> bool:
    """Return True when an env var name looks like it carries a secret.

    Matches the common ``*_API_KEY`` / ``*_TOKEN`` / ``*_SECRET`` patterns plus
    known provider prefixes (AWS_, ANTHROPIC_, OPENAI_, GITHUB_TOKEN, …).
    """
    upper = name.upper()
    if upper in _SECRET_NAME_EXACT:
        return True
    if any(pattern in upper for pattern in _SECRET_NAME_PATTERNS):
        return True
    if any(upper.startswith(prefix) for prefix in _SECRET_NAME_PREFIXES):
        return True
    return False


def build_sandbox_env(
    session_id: str | None = None,
    *,
    extra_allow: frozenset[str] | set[str] | None = None,
) -> dict[str, str]:
    """Build a scrubbed environment for the SANDBOXED execution path.

    Unlike :func:`build_subprocess_env`, this does NOT leak the full host
    environment into the sandbox. It forwards only an allowlist of benign vars
    (PATH, HOME, LANG, LC_*, TERM, TMPDIR, USER, SHELL, …) and strips anything
    that looks like a secret (``*_API_KEY``, ``*_TOKEN``, ``*_SECRET``, ``AWS_*``,
    ``ANTHROPIC_*``, ``OPENAI_*``, ``GITHUB_TOKEN`` …). Explicit session-scoped
    vars are always forwarded — the user set those on purpose for this session.
    """
    allowlist = set(SANDBOX_ENV_ALLOWLIST)
    if extra_allow:
        allowlist.update(extra_allow)

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in allowlist:
            env[key] = value
            continue
        # LC_* locale vars are safe and vary widely, so match by prefix.
        if key.startswith("LC_"):
            env[key] = value
            continue
        # Drop everything else (secrets and unknown host vars) — fail closed.

    # Session-scoped vars are explicit, per-session overrides; forward them even
    # if the name looks secret-y, but still keep valid-name discipline.
    if session_id:
        for key, value in load_session_env(session_id).items():
            if is_valid_env_name(key):
                env[key] = value
    return env
