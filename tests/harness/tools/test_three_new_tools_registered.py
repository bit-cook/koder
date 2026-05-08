"""Verify AskUserQuestion, TaskOutput, TaskStop are registered."""


def test_three_new_tools_registered():
    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    names = {getattr(t, "name", None) for t in tools}
    assert "ask_user_question" in names
    assert "task_output" in names
    assert "task_stop" in names
