import asyncio
from types import SimpleNamespace

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.mcp import MCPPrompt, MCPServerSet, close_mcp_servers


class _PromptSession:
    def __init__(self, label: str) -> None:
        self.label = label
        self.call_count = 0

    async def get_prompt(self, prompt_name, arguments=None):
        self.call_count += 1
        message = SimpleNamespace(
            role="user",
            content=SimpleNamespace(text=f"{self.label}:{prompt_name}"),
        )
        return SimpleNamespace(messages=[message], description=self.label)


class _Server:
    name = "shared"

    def __init__(self, label: str) -> None:
        self.session = _PromptSession(label)
        self.cleanup_count = 0

    async def cleanup(self):
        self.cleanup_count += 1


def _owner(label: str) -> tuple[MCPServerSet, _Server]:
    server = _Server(label)
    owner = MCPServerSet([server])
    owner.prompt_registry.register(
        MCPPrompt(server_name="shared", prompt_name="prompt", description=label)
    )
    return owner, server


def test_command_consumers_resolve_prompts_from_their_session_owner():
    first, first_server = _owner("first")
    second, second_server = _owner("second")
    first_handler = HarnessInteractiveCommandHandler(
        emit_console=False,
        mcp_owner_provider=lambda: first,
    )
    second_handler = HarnessInteractiveCommandHandler(
        emit_console=False,
        mcp_owner_provider=lambda: second,
    )

    assert dict(first_handler.get_command_list())["mcp__shared__prompt"] == "first"
    assert dict(second_handler.get_command_list())["mcp__shared__prompt"] == "second"

    async def scenario():
        async def echo_prompt(content: str, **_kwargs) -> str:
            return content

        first_result = await first_handler.handle_slash_input(
            "/mcp__shared__prompt",
            SimpleNamespace(_mcp_servers=first, handle=echo_prompt),
        )
        second_result = await second_handler.handle_slash_input(
            "/mcp__shared__prompt",
            SimpleNamespace(_mcp_servers=second, handle=echo_prompt),
        )
        await close_mcp_servers(second)
        assert "mcp__shared__prompt" in dict(first_handler.get_command_list())
        assert "mcp__shared__prompt" not in dict(second_handler.get_command_list())
        await close_mcp_servers(first)
        return first_result, second_result

    first_result, second_result = asyncio.run(scenario())

    assert "first:prompt" in first_result
    assert "second:prompt" in second_result
    assert first_server.session.call_count == 1
    assert second_server.session.call_count == 1
    assert first_server.cleanup_count == 1
    assert second_server.cleanup_count == 1


def test_case_only_mcp_prompt_commands_route_by_exact_registered_name_first():
    upper = SimpleNamespace(name="Foo", session=_PromptSession("configured-upper"))
    lower = SimpleNamespace(name="foo", session=_PromptSession("plugin-lower"))
    owner = MCPServerSet([upper, lower])
    owner.prompt_registry.register(MCPPrompt(server_name="Foo", prompt_name="probe"))
    owner.prompt_registry.register(MCPPrompt(server_name="foo", prompt_name="probe"))
    handler = HarnessInteractiveCommandHandler(
        emit_console=False,
        mcp_owner_provider=lambda: owner,
    )

    async def scenario():
        async def echo_prompt(content: str, **_kwargs) -> str:
            return content

        scheduler = SimpleNamespace(_mcp_servers=owner, handle=echo_prompt)
        upper_result = await handler.handle_slash_input("/mcp__Foo__probe", scheduler)
        lower_result = await handler.handle_slash_input("/mcp__foo__probe", scheduler)
        await close_mcp_servers(owner)
        return upper_result, lower_result

    upper_result, lower_result = asyncio.run(scenario())

    assert upper_result == "configured-upper:probe"
    assert lower_result == "plugin-lower:probe"
    assert upper.session.call_count == 1
    assert lower.session.call_count == 1
