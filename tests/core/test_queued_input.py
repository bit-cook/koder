import asyncio

import pytest
from agents import FunctionTool
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from koder_agent.core.interactive import InteractivePrompt
from koder_agent.core.queued_input import (
    ApprovalBroker,
    QueuedInputManager,
    append_queued_input_to_tool_output,
    strip_queued_input_from_tool_output,
    wrap_function_tool_for_queued_input,
)


def test_queued_input_manager_drains_visible_prompts_for_tool_result():
    manager = QueuedInputManager()

    manager.enqueue("first follow-up")
    manager.enqueue("second follow-up")

    assert manager.visible_lines() == [
        "queued: first follow-up",
        "queued: second follow-up",
    ]

    drained = manager.drain_for_tool_result()

    assert drained == ["first follow-up", "second follow-up"]
    assert manager.visible_lines() == []


def test_append_queued_input_to_tool_output_marks_user_followups():
    output = append_queued_input_to_tool_output("tool result", ["new instruction"])

    assert "tool result" in output
    assert "Queued user input" in output
    assert "new instruction" in output


def test_strip_queued_input_from_tool_output_keeps_user_display_clean():
    output = append_queued_input_to_tool_output("tool result", ["new instruction"])

    display_output = strip_queued_input_from_tool_output(output)

    assert display_output == "tool result"


def test_wrapped_function_tool_appends_queued_input_to_model_visible_output():
    manager = QueuedInputManager()
    manager.enqueue("please also inspect tests")

    async def invoke(_ctx, _input):
        return "real tool output"

    tool = FunctionTool(
        name="fake_tool",
        description="Fake tool",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=invoke,
    )

    wrap_function_tool_for_queued_input(tool, manager)
    result = asyncio.run(tool.on_invoke_tool(None, "{}"))

    assert "real tool output" in result
    assert "please also inspect tests" in result
    assert manager.visible_lines() == []


@pytest.mark.asyncio
async def test_approval_broker_serializes_concurrent_requests():
    broker = ApprovalBroker()
    broker.activate()

    first = asyncio.create_task(broker.request("first approval"))
    second = asyncio.create_task(broker.request("second approval"))
    await asyncio.sleep(0)

    assert broker.prompt == "first approval"
    assert broker.submit("y") is True
    assert await first == "y"

    await asyncio.sleep(0)
    assert broker.prompt == "second approval"
    assert broker.submit("n") is True
    assert await second == "n"
    assert broker.prompt is None


@pytest.mark.asyncio
async def test_cancelled_approval_clears_modal_and_releases_next_request():
    broker = ApprovalBroker()
    broker.activate()

    cancelled = asyncio.create_task(broker.request("cancel me"))
    waiting = asyncio.create_task(broker.request("next request"))
    await asyncio.sleep(0)
    assert broker.prompt == "cancel me"

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    await asyncio.sleep(0)
    assert broker.prompt == "next request"
    assert broker.submit("a") is True
    assert await waiting == "a"
    assert broker.prompt is None


@pytest.mark.asyncio
async def test_activation_generation_rejects_all_old_concurrent_requests():
    broker = ApprovalBroker()
    broker.activate()

    first = asyncio.create_task(broker.request("old first"))
    second = asyncio.create_task(broker.request("old second"))
    await asyncio.sleep(0)
    assert broker.prompt == "old first"

    broker.deactivate()
    broker.activate()

    assert await first == ""
    assert await second == ""
    assert broker.prompt is None
    assert broker.has_pending_request is False
    assert broker.submit("later user input") is False

    fresh = asyncio.create_task(broker.request("new request"))
    await asyncio.sleep(0)
    assert broker.prompt == "new request"
    assert broker.submit("n") is True
    assert await fresh == "n"
    assert broker.prompt is None


@pytest.mark.asyncio
async def test_streaming_prompt_routes_modal_answer_without_queue_leakage():
    prompt = InteractivePrompt(commands={})
    manager = QueuedInputManager()
    stop_event = asyncio.Event()

    with create_pipe_input() as pipe_input:
        with create_app_session(input=pipe_input, output=DummyOutput()):
            app_task = asyncio.create_task(
                prompt._run_input_app(queue_manager=manager, stop_event=stop_event)
            )
            try:
                for _ in range(100):
                    if manager.approval_broker.is_active:
                        break
                    await asyncio.sleep(0.01)
                assert manager.approval_broker.is_active

                approval_task = asyncio.create_task(
                    manager.approval_broker.request("Permission required: run_shell")
                )
                for _ in range(100):
                    if manager.approval_broker.has_pending_request:
                        break
                    await asyncio.sleep(0.01)
                assert manager.approval_broker.has_pending_request

                pipe_input.send_text("n\r")
                assert await asyncio.wait_for(approval_task, timeout=2) == "n"
                assert manager.visible_lines() == []
                assert list(prompt.history.get_strings()) == []
            finally:
                stop_event.set()
                await asyncio.wait_for(app_task, timeout=2)
