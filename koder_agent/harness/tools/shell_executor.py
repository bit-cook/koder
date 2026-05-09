"""Shell execution primitives for the harness runtime."""

from __future__ import annotations

import asyncio
import platform
import re
import shlex
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from koder_agent.harness.sandbox.backend import SandboxExecutionContext
from koder_agent.harness.sandbox.sdk_backend import execute_with_sdk_backend
from koder_agent.harness.sandbox_settings import is_excluded_command, resolve_sandbox_settings
from koder_agent.harness.session_env import build_subprocess_env

IS_WINDOWS = platform.system() == "Windows"


def resolve_powershell_executable() -> str | None:
    """Return a PowerShell executable available on this machine."""

    candidates = ["pwsh", "powershell"]
    if IS_WINDOWS:
        candidates.append("powershell.exe")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


@dataclass
class BackgroundProcess:
    """Background shell process state."""

    shell_id: str
    command: str
    process: asyncio.subprocess.Process
    start_time: float
    output_lines: list[str] = field(default_factory=list)
    last_read_index: int = 0
    status: str = "running"
    exit_code: int | None = None

    def add_output(self, line: str) -> None:
        self.output_lines.append(line)

    def get_new_output(self, filter_pattern: str | None = None) -> list[str]:
        new_lines = self.output_lines[self.last_read_index :]
        self.last_read_index = len(self.output_lines)
        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern)
                new_lines = [line for line in new_lines if pattern.search(line)]
            except re.error:
                pass
        return new_lines


class BackgroundProcessManager:
    """Singleton-style manager for runtime background shells."""

    _shells: dict[str, BackgroundProcess] = {}
    _monitor_tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def add(cls, shell: BackgroundProcess) -> None:
        cls._shells[shell.shell_id] = shell

    @classmethod
    def get(cls, shell_id: str) -> BackgroundProcess | None:
        return cls._shells.get(shell_id)

    @classmethod
    def list_ids(cls) -> list[str]:
        return list(cls._shells.keys())

    @classmethod
    async def start_monitor(cls, shell_id: str) -> None:
        shell = cls.get(shell_id)
        if not shell:
            return

        async def monitor() -> None:
            try:
                process = shell.process
                while True:
                    if not process.stdout:
                        break
                    try:
                        line = await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0.05)
                        continue
                    if line:
                        shell.add_output(line.decode("utf-8", errors="replace").rstrip("\n"))
                        continue
                    if process.returncode is not None:
                        break
                    await asyncio.sleep(0.05)

                shell.exit_code = await process.wait()
                shell.status = "completed" if shell.exit_code == 0 else "failed"
            except Exception as exc:
                shell.status = "error"
                shell.add_output(f"Monitor error: {exc}")
            finally:
                cls._monitor_tasks.pop(shell_id, None)

        cls._monitor_tasks[shell_id] = asyncio.create_task(monitor())

    @classmethod
    async def terminate(cls, shell_id: str) -> BackgroundProcess:
        shell = cls.get(shell_id)
        if not shell:
            raise ValueError(f"Shell not found: {shell_id}")
        if shell.process.returncode is None:
            shell.process.terminate()
            try:
                await asyncio.wait_for(shell.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                shell.process.kill()
                await shell.process.wait()
        shell.status = "terminated"
        shell.exit_code = shell.process.returncode
        monitor = cls._monitor_tasks.pop(shell_id, None)
        if monitor and not monitor.done():
            monitor.cancel()
        cls._shells.pop(shell_id, None)
        return shell


@dataclass(frozen=True)
class ShellExecutionResult:
    """Result of a shell execution request."""

    status: str
    output: str
    exit_code: int | None = None
    shell_id: str | None = None


def _sandbox_output(result) -> str:
    lines: list[str] = []
    if result.sandboxed:
        lines.append("sandboxed: true")
        if result.backend_id:
            lines.append(f"backend: {result.backend_id}")
    body = result.combined_output().strip()
    if body:
        lines.append(body)
    return "\n".join(lines) if lines else "(no output)"


async def execute_shell_command(
    command: str,
    *,
    timeout: int = 120,
    run_in_background: bool = False,
    session_id: str | None = None,
) -> ShellExecutionResult:
    """Execute a shell command without performing permission checks."""
    parts = shlex.split(command)
    if not parts:
        return ShellExecutionResult(status="error", output="Empty command")

    timeout = max(1, min(timeout, 600))

    sandbox_state = resolve_sandbox_settings(Path.cwd())
    use_sandbox = sandbox_state.enabled and not is_excluded_command(command, cwd=Path.cwd())
    if use_sandbox:
        if run_in_background:
            return ShellExecutionResult(
                status="error",
                output=(
                    "sandboxed: false\n"
                    f"backend: {sandbox_state.backend}\n"
                    "reason: background sandbox execution is not implemented"
                ),
            )
        elif sandbox_state.policy is not None:
            child_env = build_subprocess_env(session_id)
            sandbox_result = await execute_with_sdk_backend(
                SandboxExecutionContext(
                    cwd=Path.cwd().resolve(),
                    repo_root=Path.cwd().resolve(),
                    command=command,
                    env=child_env,
                    timeout=timeout,
                    background=False,
                    session_id=session_id,
                    policy=sandbox_state.policy,
                )
            )
            if sandbox_result.status not in {"unavailable", "unsupported"}:
                return ShellExecutionResult(
                    status="success" if sandbox_result.exit_code == 0 else "error",
                    output=_sandbox_output(sandbox_result),
                    exit_code=sandbox_result.exit_code,
                )
            return ShellExecutionResult(
                status="error",
                output=(
                    "sandboxed: false\n"
                    f"backend: {sandbox_state.backend}\n"
                    f"reason: {sandbox_result.reason or 'sandbox backend unavailable'}"
                ),
            )

    if run_in_background:
        shell_id = str(uuid.uuid4())[:8]
        child_env = build_subprocess_env(session_id)
        if IS_WINDOWS:
            process = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-Command",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=child_env,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=child_env,
            )
        shell = BackgroundProcess(
            shell_id=shell_id,
            command=command,
            process=process,
            start_time=time.time(),
        )
        BackgroundProcessManager.add(shell)
        await BackgroundProcessManager.start_monitor(shell_id)
        return ShellExecutionResult(
            status="background",
            output=(
                f"Command started in background.\n"
                f"shell_id: {shell_id}\n"
                f"Use shell_output(shell_id='{shell_id}') to monitor output.\n"
                f"Use shell_kill(shell_id='{shell_id}') to terminate."
            ),
            shell_id=shell_id,
        )

    child_env = build_subprocess_env(session_id)
    if IS_WINDOWS:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
        )
    else:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
        )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        return ShellExecutionResult(
            status="error",
            output=f"Command timed out after {timeout} seconds",
        )

    output = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if stderr_text:
        output += f"\n[stderr]: {stderr_text}" if output else f"[stderr]: {stderr_text}"
    if process.returncode != 0:
        output += (
            f"\n[exit code]: {process.returncode}"
            if output
            else f"[exit code]: {process.returncode}"
        )
    return ShellExecutionResult(
        status="success" if process.returncode == 0 else "error",
        output=output or "(no output)",
        exit_code=process.returncode,
    )


async def execute_powershell_command(
    command: str,
    *,
    timeout: int = 120,
    run_in_background: bool = False,
    session_id: str | None = None,
) -> ShellExecutionResult:
    """Execute a PowerShell command without performing permission checks."""

    if not command.strip():
        return ShellExecutionResult(status="error", output="Empty command")

    executable = resolve_powershell_executable()
    if executable is None:
        return ShellExecutionResult(
            status="error",
            output="PowerShell executable not found. Install PowerShell (pwsh) or run on Windows.",
        )

    timeout = max(1, min(timeout, 600))
    argv = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    child_env = build_subprocess_env(session_id)

    if run_in_background:
        shell_id = str(uuid.uuid4())[:8]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=child_env,
        )
        shell = BackgroundProcess(
            shell_id=shell_id,
            command=f"powershell: {command}",
            process=process,
            start_time=time.time(),
        )
        BackgroundProcessManager.add(shell)
        await BackgroundProcessManager.start_monitor(shell_id)
        return ShellExecutionResult(
            status="background",
            output=(
                f"PowerShell command started in background.\n"
                f"shell_id: {shell_id}\n"
                f"Use shell_output(shell_id='{shell_id}') to monitor output.\n"
                f"Use shell_kill(shell_id='{shell_id}') to terminate."
            ),
            shell_id=shell_id,
        )

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        return ShellExecutionResult(
            status="error",
            output=f"PowerShell command timed out after {timeout} seconds",
        )

    output = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if stderr_text:
        output += f"\n[stderr]: {stderr_text}" if output else f"[stderr]: {stderr_text}"
    if process.returncode != 0:
        output += (
            f"\n[exit code]: {process.returncode}"
            if output
            else f"[exit code]: {process.returncode}"
        )

    return ShellExecutionResult(
        status="success" if process.returncode == 0 else "error",
        output=output or "(no output)",
        exit_code=process.returncode,
    )


async def get_background_output(
    shell_id: str, filter_str: str | None = None
) -> ShellExecutionResult:
    """Get incremental output for a background shell."""
    shell = BackgroundProcessManager.get(shell_id)
    if not shell:
        available = BackgroundProcessManager.list_ids()
        return ShellExecutionResult(
            status="error",
            output=f"Shell not found: {shell_id}\nAvailable: {available or 'none'}",
        )

    new_lines = shell.get_new_output(filter_pattern=filter_str)
    output = "\n".join(new_lines) if new_lines else "(no new output)"
    status_info = f"\n[status]: {shell.status}"
    if shell.exit_code is not None:
        status_info += f"\n[exit_code]: {shell.exit_code}"
    return ShellExecutionResult(status="success", output=output + status_info, shell_id=shell_id)


async def terminate_background_command(shell_id: str) -> ShellExecutionResult:
    """Terminate a background shell and return its final output summary."""
    shell = BackgroundProcessManager.get(shell_id)
    remaining_lines = shell.get_new_output() if shell else []
    try:
        shell = await BackgroundProcessManager.terminate(shell_id)
    except ValueError as exc:
        available = BackgroundProcessManager.list_ids()
        return ShellExecutionResult(
            status="error",
            output=f"{exc}\nAvailable: {available or 'none'}",
        )
    output = "\n".join(remaining_lines) if remaining_lines else "(no remaining output)"
    return ShellExecutionResult(
        status="success",
        output=f"Shell {shell_id} terminated.\n{output}\n[exit_code]: {shell.exit_code}",
        exit_code=shell.exit_code,
        shell_id=shell_id,
    )
