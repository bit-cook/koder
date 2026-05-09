"""PowerShell command execution tool."""

from __future__ import annotations

from pydantic import BaseModel

from koder_agent.harness.permissions.powershell_classifier import classify_powershell_command
from koder_agent.harness.tools.shell_executor import execute_powershell_command

from .compat import function_tool


class PowerShellModel(BaseModel):
    command: str
    timeout: int = 120
    run_in_background: bool = False


@function_tool
async def run_powershell(command: str, timeout: int = 120, run_in_background: bool = False) -> str:
    """Execute a PowerShell command with Koder permission and background support.

    Args:
        command: The PowerShell command to execute.
        timeout: Timeout in seconds for foreground commands. The value is clamped to 1-600.
        run_in_background: Set true for long-running commands. Use shell_output to monitor.

    Returns:
        Command output, or a shell_id if run_in_background=True.
    """

    decision = classify_powershell_command(command)
    if not decision.allowed:
        return decision.reason

    result = await execute_powershell_command(
        command,
        timeout=timeout,
        run_in_background=run_in_background,
    )
    return result.output
