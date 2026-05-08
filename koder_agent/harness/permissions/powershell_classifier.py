"""Static PowerShell command classification for permission decisions."""

from __future__ import annotations

import re

from .shell_classifier import ShellCommandDecision

READ_ONLY_CMDLETS = {
    "echo",
    "format-list",
    "format-table",
    "get-acl",
    "get-childitem",
    "get-command",
    "get-content",
    "get-filehash",
    "get-item",
    "get-location",
    "get-process",
    "get-service",
    "get-variable",
    "measure-object",
    "resolve-path",
    "select-object",
    "select-string",
    "sort-object",
    "test-path",
    "where-object",
    "write-host",
    "write-output",
}

READ_ONLY_ALIASES = {
    "cat": "get-content",
    "cd": "get-location",
    "dir": "get-childitem",
    "echo": "write-output",
    "gci": "get-childitem",
    "gc": "get-content",
    "gl": "get-location",
    "gp": "get-item",
    "ls": "get-childitem",
    "pwd": "get-location",
    "select": "select-object",
    "sls": "select-string",
    "where": "where-object",
}

WRITE_CMDLETS = {
    "add-content",
    "clear-content",
    "copy-item",
    "mkdir",
    "move-item",
    "new-item",
    "out-file",
    "remove-item",
    "rename-item",
    "rm",
    "rmdir",
    "set-acl",
    "set-content",
    "set-item",
    "set-itemproperty",
    "start-process",
}

DANGEROUS_CMDLETS = {
    "clear-disk",
    "format-volume",
    "iex",
    "invoke-expression",
    "restart-computer",
    "set-executionpolicy",
    "stop-computer",
}

COMMAND_SPLIT_PATTERN = re.compile(r"\s*(?:\|\||&&|[|;\r\n])\s*")
WRITE_REDIRECTION_PATTERN = re.compile(r"(?:^|[^\d])>>?(?!\s*\$null\b)")


def _canonical_cmdlet(segment: str) -> str:
    stripped = segment.strip()
    if not stripped:
        return ""
    name = stripped.split(None, 1)[0].strip("'\"").lower()
    return READ_ONLY_ALIASES.get(name, name)


def classify_powershell_command(command: str) -> ShellCommandDecision:
    """Classify a PowerShell command into read-only, mutating, or blocked states."""

    raw = command.strip()
    if not raw:
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=False,
            destructive=False,
            malformed=True,
            reason="empty command",
        )

    segments = [segment for segment in COMMAND_SPLIT_PATTERN.split(raw) if segment.strip()]
    cmdlets = [_canonical_cmdlet(segment) for segment in segments]
    cmdlets = [cmdlet for cmdlet in cmdlets if cmdlet]
    if not cmdlets:
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=False,
            destructive=False,
            malformed=True,
            reason="empty command",
        )

    if any(cmdlet in DANGEROUS_CMDLETS for cmdlet in cmdlets):
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=True,
            destructive=True,
            malformed=False,
            reason="dangerous PowerShell command detected",
        )

    read_only = all(cmdlet in READ_ONLY_CMDLETS for cmdlet in cmdlets)
    mutates = any(cmdlet in WRITE_CMDLETS for cmdlet in cmdlets) or bool(
        WRITE_REDIRECTION_PATTERN.search(raw)
    )
    if read_only and not mutates:
        return ShellCommandDecision(
            command=command,
            allowed=True,
            read_only=True,
            requires_approval=False,
            destructive=False,
            malformed=False,
            reason="read-only PowerShell command",
        )

    return ShellCommandDecision(
        command=command,
        allowed=True,
        read_only=False,
        requires_approval=True,
        destructive=False,
        malformed=False,
        reason="PowerShell command may mutate filesystem or execute code",
    )
