import asyncio

from agents import FunctionTool

from koder_agent.core.queued_input import (
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
