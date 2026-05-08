import pytest

from koder_agent.harness.cli.entrypoint import RuntimeRequest, run_harness_runtime


@pytest.mark.asyncio
async def test_interactive_runtime_does_not_call_legacy_session_flow(monkeypatch):
    async def ok_harness(*_args, **_kwargs):
        return 0

    monkeypatch.setattr("koder_agent.harness.runtime.run_harness_session_flow", ok_harness)
    result = await run_harness_runtime(RuntimeRequest(argv=[], mode="interactive"))
    assert result == 0


@pytest.mark.asyncio
async def test_prompt_runtime_does_not_call_legacy_session_flow(monkeypatch):
    async def ok_harness(*_args, **_kwargs):
        return 0

    monkeypatch.setattr("koder_agent.harness.runtime.run_harness_session_flow", ok_harness)
    result = await run_harness_runtime(RuntimeRequest(argv=["hello"], mode="prompt"))
    assert result == 0
