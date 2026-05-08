"""Tests for plugin state persistence."""

import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.plugins.state import PluginState, PluginStateStore  # noqa: E402


def test_state_store_get_returns_none_for_unknown(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    assert store.get("nonexistent") is None


def test_state_store_set_and_get(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    state = PluginState(enabled=True, scope="user")
    store.set("my-plugin", state)
    loaded = store.get("my-plugin")
    assert loaded is not None
    assert loaded.enabled is True
    assert loaded.scope == "user"
    assert loaded.installed_at != ""


def test_state_store_remove(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    store.set("to-remove", PluginState())
    assert store.remove("to-remove") is True
    assert store.get("to-remove") is None
    assert store.remove("to-remove") is False  # Already removed


def test_state_store_is_enabled_default(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    # No state record → default enabled
    assert store.is_enabled("unknown-plugin") is True


def test_state_store_is_enabled_tracks_state(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    store.set("my-plugin", PluginState(enabled=False))
    assert store.is_enabled("my-plugin") is False
    store.set("my-plugin", PluginState(enabled=True))
    assert store.is_enabled("my-plugin") is True


def test_state_store_list_all(tmp_path):
    store = PluginStateStore.for_test(tmp_path)
    store.set("plugin-a", PluginState(enabled=True, scope="user"))
    store.set("plugin-b", PluginState(enabled=False, scope="project"))
    all_states = store.list_all()
    assert len(all_states) == 2
    assert all_states["plugin-a"].enabled is True
    assert all_states["plugin-b"].enabled is False
    assert all_states["plugin-b"].scope == "project"


def test_state_store_persists_across_instances(tmp_path):
    """State survives recreation of the store object."""
    store1 = PluginStateStore.for_test(tmp_path)
    store1.set("persistent", PluginState(enabled=True, scope="local"))

    store2 = PluginStateStore.for_test(tmp_path)
    loaded = store2.get("persistent")
    assert loaded is not None
    assert loaded.scope == "local"
