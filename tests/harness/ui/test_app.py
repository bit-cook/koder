from koder_agent.harness.state.store import HarnessStore
from koder_agent.harness.ui.app import HarnessApp
from koder_agent.harness.ui.widgets.prompt_buffer import render_prompt_buffer
from koder_agent.harness.ui.widgets.status_line import render_status_line


def test_harness_app_renders_from_store():
    app = HarnessApp(store=HarnessStore.initial())
    frame = app.render_frame()
    assert frame["screen"] == "main"
    assert frame["sections"] == ["status_line", "prompt_buffer"]
    assert frame["mode"] == HarnessStore.initial().state.mode


def test_harness_ui_widgets_render_metadata():
    status = render_status_line(mode="interactive", session_id="s1", model="gpt-4o")
    prompt = render_prompt_buffer(text="hello")

    assert status == {
        "section": "status_line",
        "mode": "interactive",
        "session_id": "s1",
        "model": "gpt-4o",
    }
    assert prompt == {"section": "prompt_buffer", "title": "Koder", "text": "hello"}
