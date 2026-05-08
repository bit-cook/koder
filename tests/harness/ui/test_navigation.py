from koder_agent.harness.state.store import HarnessStore
from koder_agent.harness.ui.app import HarnessApp


def test_harness_app_dispatches_navigation_action():
    app = HarnessApp(store=HarnessStore.initial())
    result = app.handle_key("tab")
    assert result.dispatched_action is not None
