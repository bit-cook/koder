"""Harness shell tool wrappers."""

from __future__ import annotations

from ..permissions.powershell_classifier import classify_powershell_command
from ..permissions.shell_classifier import classify_shell_command
from .registry import ToolRegistry, ToolSpec, build_tool_result
from .shell_executor import (
    execute_powershell_command,
    execute_shell_command,
    get_background_output,
    terminate_background_command,
)


async def invoke_run_shell(arguments: dict) -> dict:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return build_tool_result("run_shell", "Missing required argument: command", status="error")

    decision = classify_shell_command(command)
    if not decision.allowed:
        return build_tool_result("run_shell", decision.reason, status="error")

    result = await execute_shell_command(
        command,
        timeout=arguments.get("timeout", 120),
        run_in_background=bool(arguments.get("run_in_background", False)),
    )
    return build_tool_result(
        "run_shell",
        result.output,
        status=result.status if result.status != "background" else "success",
    )


async def invoke_run_powershell(arguments: dict) -> dict:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return build_tool_result(
            "run_powershell", "Missing required argument: command", status="error"
        )

    decision = classify_powershell_command(command)
    if not decision.allowed:
        return build_tool_result("run_powershell", decision.reason, status="error")

    result = await execute_powershell_command(
        command,
        timeout=arguments.get("timeout", 120),
        run_in_background=bool(arguments.get("run_in_background", False)),
    )
    return build_tool_result(
        "run_powershell",
        result.output,
        status=result.status if result.status != "background" else "success",
    )


async def invoke_shell_output(arguments: dict) -> dict:
    shell_id = arguments.get("shell_id")
    if not isinstance(shell_id, str):
        return build_tool_result(
            "shell_output", "Missing required argument: shell_id", status="error"
        )
    result = await get_background_output(shell_id, filter_str=arguments.get("filter_str"))
    return build_tool_result("shell_output", result.output, status=result.status)


async def invoke_shell_kill(arguments: dict) -> dict:
    shell_id = arguments.get("shell_id")
    if not isinstance(shell_id, str):
        return build_tool_result(
            "shell_kill", "Missing required argument: shell_id", status="error"
        )
    result = await terminate_background_command(shell_id)
    return build_tool_result("shell_kill", result.output, status=result.status)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name="run_shell", invoke=invoke_run_shell, category="tool"))
    registry.register(
        ToolSpec(name="run_powershell", invoke=invoke_run_powershell, category="tool")
    )
    registry.register(ToolSpec(name="shell_output", invoke=invoke_shell_output, category="tool"))
    registry.register(ToolSpec(name="shell_kill", invoke=invoke_shell_kill, category="tool"))
