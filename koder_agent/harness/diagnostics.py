"""Shared diagnostics (doctor) helpers usable from interactive and headless CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def detect_installation_type() -> str:
    """Return how koder appears to be installed (development/local/unknown)."""
    repo_root = Path(__file__).resolve().parents[2]
    executable = Path(sys.executable).resolve()
    argv0 = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else executable

    if (repo_root / "pyproject.toml").exists() and str(repo_root) in {
        *map(str, executable.parents),
        *map(str, argv0.parents),
    }:
        return "development"
    if ".venv" in str(executable):
        return "local"
    return "unknown"


def detect_invoked_binary() -> str:
    """Return the resolved path to the invoked entrypoint binary."""
    candidate = sys.argv[0] if sys.argv and sys.argv[0] else sys.executable
    try:
        return str(Path(candidate).expanduser().resolve())
    except Exception:
        return candidate


def detect_ripgrep_status() -> tuple[bool, str, str]:
    """Return (working, mode, path) for the ripgrep binary."""
    rg_path = shutil.which("rg")
    if not rg_path:
        return False, "missing", "not found"
    try:
        proc = subprocess.run(
            [rg_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return True, "system", rg_path
    except Exception:
        pass
    return False, "system", rg_path


async def collect_doctor_report() -> dict[str, object]:
    """Collect the diagnostic report as a plain dictionary.

    Shared by the interactive `/doctor` command and the top-level
    `koder doctor` subcommand so both surfaces stay in sync.
    """
    from koder_agent.config import get_config
    from koder_agent.mcp.server_manager import MCPServerManager

    config = get_config()
    mcp_manager = MCPServerManager()
    servers = await mcp_manager.list_servers(cwd=os.getcwd())
    rg_working, rg_mode, rg_path = detect_ripgrep_status()
    return {
        "cwd": os.getcwd(),
        "python": sys.executable,
        "installation_type": detect_installation_type(),
        "invoked_binary": detect_invoked_binary(),
        "config_path": str(Path.home() / ".koder" / "config.yaml"),
        "model": config.model.name,
        "provider": config.model.provider,
        "permission_mode": config.harness.permission_mode,
        "mcp_servers": len(servers),
        "ripgrep_working": rg_working,
        "ripgrep_mode": rg_mode,
        "ripgrep_path": rg_path,
    }


def render_doctor_text(report: dict[str, object]) -> str:
    """Render the doctor report as the human-readable key: value text block."""
    return (
        f"cwd: {report['cwd']}\n"
        f"python: {report['python']}\n"
        f"installation_type: {report['installation_type']}\n"
        f"invoked_binary: {report['invoked_binary']}\n"
        f"config_path: {report['config_path']}\n"
        f"model: {report['model']}\n"
        f"provider: {report['provider']}\n"
        f"permission_mode: {report['permission_mode']}\n"
        f"mcp_servers: {report['mcp_servers']}\n"
        f"ripgrep_working: {str(report['ripgrep_working']).lower()}\n"
        f"ripgrep_mode: {report['ripgrep_mode']}\n"
        f"ripgrep_path: {report['ripgrep_path']}"
    )


def redact_doctor_report(report: dict[str, object]) -> dict[str, object]:
    """Return a JSON-safe, redacted copy of the doctor report.

    Filesystem paths that may leak the current user/home are collapsed to a
    home-relative form to avoid disclosing absolute local paths in machine
    output.
    """
    home = str(Path.home())

    def _redact_path(value: object) -> object:
        if isinstance(value, str) and value.startswith(home):
            return "~" + value[len(home) :]
        return value

    redacted = dict(report)
    for key in ("cwd", "python", "invoked_binary", "config_path", "ripgrep_path"):
        if key in redacted:
            redacted[key] = _redact_path(redacted[key])
    return redacted
