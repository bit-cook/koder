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
    """Build child-process environment with optional session-scoped overrides."""
    env = os.environ.copy()
    if session_id:
        env.update(load_session_env(session_id))
    return env
