from koder_agent.harness.state.actions import AppendNotification, SetMode
from koder_agent.harness.state.store import HarnessStore


def test_store_applies_actions_without_direct_mutation():
    store = HarnessStore.initial()
    store.dispatch(SetMode(mode="interactive"))
    store.dispatch(AppendNotification(message="ready"))
    assert store.state.mode == "interactive"
    assert store.state.notifications[-1].message == "ready"
