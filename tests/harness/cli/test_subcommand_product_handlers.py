from __future__ import annotations

import asyncio

from koder_agent.harness.cli.entrypoint import RuntimeRequest, run_harness_runtime


def test_runtime_request_classifies_config_mcp_agents_and_plugin_as_subcommands():
    from koder_agent.harness.cli.entrypoint import build_runtime_request

    assert build_runtime_request(["config", "show"]).mode == "subcommand"
    assert build_runtime_request(["mcp", "list"]).mode == "subcommand"
    assert build_runtime_request(["agents"]).mode == "subcommand"
    assert build_runtime_request(["plugin", "list"]).mode == "subcommand"


def test_subcommand_runtime_uses_harness_session_flow(monkeypatch):
    async def fake_harness(*_args, **_kwargs):
        return 0

    monkeypatch.setattr("koder_agent.harness.runtime.run_harness_session_flow", fake_harness)
    result = asyncio.run(
        run_harness_runtime(RuntimeRequest(argv=["config", "show"], mode="subcommand"))
    )
    assert result == 0
