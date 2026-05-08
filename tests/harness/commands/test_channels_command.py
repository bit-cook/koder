import asyncio

import pytest

from koder_agent.harness.channels.state import (
    reset_channel_state,
    set_allowed_channels,
    set_has_dev_channels,
)
from koder_agent.harness.channels.types import ChannelEntryPlugin, ChannelEntryServer
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


@pytest.fixture(autouse=True)
def clean_channel_state():
    reset_channel_state()
    yield
    reset_channel_state()


def _run(command: str, *, handler: HarnessInteractiveCommandHandler | None = None) -> str:
    handler = handler or HarnessInteractiveCommandHandler(emit_console=False)
    return asyncio.run(handler.handle_slash_input(command, scheduler=None))


def test_channels_command_reports_empty_runtime_state():
    output = _run("/channels")

    assert "channels:" in output
    assert "enabled: false" in output
    assert "configured: 0" in output
    assert "usage: uv run koder --channels server:<name>" in output
    assert "plugin_usage: uv run koder --channels plugin:<name>@<marketplace>" in output


def test_channels_command_lists_server_and_plugin_entries():
    set_allowed_channels(
        [
            ChannelEntryServer(name="test-channel"),
            ChannelEntryPlugin(name="team-chat", marketplace="local", dev=True),
        ]
    )
    set_has_dev_channels(True)

    output = _run("/channels")

    assert "enabled: true" in output
    assert "configured: 2" in output
    assert "development_channels: true" in output
    assert "- server:test-channel" in output
    assert "- plugin:team-chat@local [development]" in output


def test_channels_command_rejects_mutation_arguments():
    assert _run("/channels install team-chat") == "Usage: /channels"
    assert "Usage: /channels" in _run("/channels help")
