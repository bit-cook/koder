"""Tmux backend for spawning teammate koder processes.

Each teammate runs as a separate koder process in its own tmux pane,
enabling true multi-process parallel execution.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from os import environ
from typing import Mapping

INHERITED_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "AZURE_API_BASE",
    "AZURE_API_KEY",
    "AZURE_API_VERSION",
    "CLOUDFLARE_API_KEY",
    "COHERE_API_KEY",
    "DEEPINFRA_API_KEY",
    "FIREWORKS_AI_API_KEY",
    "GEMINI_API_KEY",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GROQ_API_KEY",
    "HOME",
    "HUGGINGFACE_API_KEY",
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "MISTRAL_API_KEY",
    "OLLAMA_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
    "PATH",
    "PERPLEXITYAI_API_KEY",
    "PYTHONPATH",
    "REPLICATE_API_TOKEN",
    "TOGETHERAI_API_KEY",
    "VIRTUAL_ENV",
    "XDG_CONFIG_HOME",
}


def is_tmux_available() -> bool:
    """Check if tmux is installed and available."""
    return shutil.which("tmux") is not None


def get_tmux_session_name(team_name: str) -> str:
    """Generate a tmux session name for a team."""
    return f"koder-{team_name}"


def get_current_tmux_session_name(env: Mapping[str, str] | None = None) -> str | None:
    """Return the current tmux session name when Koder is already running in tmux."""
    current_env = env if env is not None else environ
    if not current_env.get("TMUX"):
        return None
    result = subprocess.run(
        ["tmux", "display-message", "-p", "#S"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    session_name = result.stdout.strip()
    return session_name or None


@dataclass
class TmuxPane:
    """Represents a tmux pane running a teammate."""

    pane_id: str
    session_name: str
    member_name: str
    pid: int | None = None


class TmuxBackend:
    """Manages teammate processes via tmux panes.

    Usage:
        backend = TmuxBackend(session_name="my-team")
        pane = backend.spawn_member("worker-1", prompt="Fix auth", cwd="/project")
        backend.send_keys(pane, "/compact")
        backend.kill_member(pane)
        backend.cleanup()
    """

    def __init__(self, session_name: str):
        self.session_name = session_name
        self._panes: dict[str, TmuxPane] = {}

    def _session_exists(self) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    def _enable_remain_on_exit(self) -> None:
        """Keep completed teammate panes visible for debugging and state inspection."""
        subprocess.run(
            ["tmux", "set-option", "-w", "-t", self.session_name, "remain-on-exit", "on"],
            capture_output=True,
            timeout=5,
        )

    def spawn_member(
        self,
        name: str,
        prompt: str,
        cwd: str = ".",
        model: str | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: list[str] | None = None,
    ) -> TmuxPane:
        """Spawn a new teammate in a tmux pane.

        Args:
            name: Teammate name (used as pane title).
            prompt: Initial prompt for the koder session.
            cwd: Working directory for the session.
            model: Optional model override.
            env: Environment values inherited from the leader process.
            extra_args: Additional CLI args for koder.

        Returns:
            TmuxPane representing the spawned teammate.
        """
        current_env = env if env is not None else environ
        child_env = {
            key: value
            for key, value in current_env.items()
            if key.startswith("KODER_") or key in INHERITED_ENV_KEYS
        }
        if model:
            child_env["KODER_MODEL"] = model
        env_prefix = "".join(
            f"{key}={shlex.quote(value)} " for key, value in sorted(child_env.items())
        )
        # Re-enter the currently running Python package so source checkouts and
        # installed console scripts use the same runtime and environment.
        koder_cmd = (
            f"cd {shlex.quote(cwd)} && {env_prefix}{shlex.quote(sys.executable)} -m koder_agent.cli"
        )
        if extra_args:
            koder_cmd += " " + " ".join(shlex.quote(arg) for arg in extra_args)
        koder_cmd += f" {shlex.quote(prompt)}"

        if self._session_exists():
            self._enable_remain_on_exit()
            command = [
                "tmux",
                "split-window",
                "-h",
                "-d",
                "-t",
                self.session_name,
                "-P",
                "-F",
                "#{pane_id}",
                koder_cmd,
            ]
        else:
            command = [
                "tmux",
                "new-session",
                "-d",
                "-s",
                self.session_name,
                "-c",
                cwd,
                "-P",
                "-F",
                "#{pane_id}",
                koder_cmd,
            ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to spawn tmux pane: {result.stderr.strip()}")

        pane_id = result.stdout.strip()
        pane = TmuxPane(
            pane_id=pane_id,
            session_name=self.session_name,
            member_name=name,
        )
        self._panes[name] = pane
        return pane

    def kill_member(self, pane: TmuxPane) -> None:
        """Kill a teammate's tmux pane."""
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane.pane_id],
            capture_output=True,
            timeout=5,
        )
        self._panes.pop(pane.member_name, None)

    def send_keys(self, pane: TmuxPane, text: str) -> None:
        """Send text input to a teammate's tmux pane."""
        subprocess.run(
            ["tmux", "send-keys", "-t", pane.pane_id, "-l", text],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", pane.pane_id, "Enter"],
            capture_output=True,
            timeout=5,
        )

    def capture_output(self, pane: TmuxPane, lines: int = 50) -> str:
        """Capture recent output from a teammate's pane."""
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane.pane_id, "-p", "-S", f"-{lines}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""

    def list_members(self) -> list[TmuxPane]:
        """List all active teammate panes."""
        return list(self._panes.values())

    def cleanup(self) -> None:
        """Kill all teammate panes."""
        for pane in list(self._panes.values()):
            try:
                self.kill_member(pane)
            except Exception:
                pass
        self._panes.clear()
