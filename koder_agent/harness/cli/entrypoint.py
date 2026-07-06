"""Typed runtime requests for the new harness runtime."""

from __future__ import annotations

from dataclasses import dataclass

from koder_agent.harness.runtime import HarnessRuntime


@dataclass(frozen=True)
class RuntimeRequest:
    argv: list[str]
    mode: str
    help_text: str | None = None
    first_arg: str | None = None


SUBCOMMANDS = {
    "auth",
    "mcp",
    "config",
    "agents",
    "plugin",
    "plugins",
    "doctor",
    "review",
    "completion",
    "upgrade",
}
FLAGS_WITH_VALUE = {
    "--session",
    "-s",
    "--output-format",
    "--json-schema",
    "--system-prompt",
    "--system-prompt-file",
    "--append-system-prompt",
    "--append-system-prompt-file",
    "--bare",
    "--allowedTools",
    "--input-format",
    "--name",
    "-n",
    "--agents",
    "--agent",
    "--teammate-mode",
    "--channels",
    "--dangerously-load-development-channels",
}
OPTIONAL_VALUE_FLAGS = {"--resume", "-r"}


def detect_first_arg(argv: list[str]) -> str | None:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"-p", "--print"}:
            return None
        if not token.startswith("-"):
            return token
        if token in FLAGS_WITH_VALUE:
            index += 2
            continue
        if token in OPTIONAL_VALUE_FLAGS:
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
            continue
        index += 1
    return None


def build_runtime_request(argv: list[str]) -> RuntimeRequest:
    if not argv:
        return RuntimeRequest(argv=[], mode="interactive", first_arg=None)
    first = argv[0]
    if first in {"-h", "--help"}:
        return RuntimeRequest(argv=argv, mode="help", first_arg=None)
    if first in {"-V", "-v", "--version"}:
        return RuntimeRequest(argv=argv, mode="version", first_arg=None)

    first_arg = detect_first_arg(argv)
    if first_arg == "auth":
        return RuntimeRequest(argv=argv, mode="auth_passthrough", first_arg=first_arg)
    if first_arg in SUBCOMMANDS:
        return RuntimeRequest(argv=argv, mode="subcommand", first_arg=first_arg)
    return RuntimeRequest(argv=argv, mode="prompt", first_arg=first_arg)


async def run_harness_runtime(request: RuntimeRequest) -> int:
    runtime = HarnessRuntime(request=request)
    return await runtime.run()
