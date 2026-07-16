"""Tests for plugin state persistence."""

import json
import sys
import types
from pathlib import Path

import pytest

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


def test_state_store_refuses_symlink_without_overwriting_target(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")
    (tmp_path / "state.json").symlink_to(outside)
    store = PluginStateStore.for_test(tmp_path)

    with pytest.raises(OSError, match="symlinked plugin state"):
        store.set("demo", PluginState())

    assert outside.read_text(encoding="utf-8") == '{"sentinel": true}'


def test_state_store_parent_path_swap_stays_pinned_to_original_directory(tmp_path):
    root = tmp_path / "state-root"
    store = PluginStateStore.for_test(root)
    pinned_root = tmp_path / "pinned-state-root"
    outside = tmp_path / "outside-state-root"
    outside.mkdir()
    root.rename(pinned_root)
    root.symlink_to(outside, target_is_directory=True)

    store.set("demo", PluginState(enabled=False))

    assert not (outside / "state.json").exists()
    data = json.loads((pinned_root / "state.json").read_text(encoding="utf-8"))
    assert data["demo"]["enabled"] is False


def test_state_store_atomic_replace_failure_preserves_previous_data(tmp_path, monkeypatch):
    store = PluginStateStore.for_test(tmp_path)
    store.set("demo", PluginState(enabled=False))
    state_module = sys.modules[PluginStateStore.__module__]

    def fail_replace(_source, _target, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.set("demo", PluginState(enabled=True))

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["demo"]["enabled"] is False
    assert not list(tmp_path.glob(".state.json-*.tmp"))


def test_state_store_quarantines_legacy_case_alias_without_blocking_canonical_state(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"Demo": {"enabled": False}, "demo": {"enabled": True}}),
        encoding="utf-8",
    )
    store = PluginStateStore.for_test(tmp_path)

    states = store.list_all()

    assert states["demo"].enabled is True
    assert json.loads((tmp_path / "state.legacy.json").read_text(encoding="utf-8")) == {
        "Demo": {"enabled": False}
    }
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {
        "demo": {"enabled": True}
    }


def test_state_store_migrates_unambiguous_legacy_case(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"Demo": {"enabled": False, "scope": "project"}}),
        encoding="utf-8",
    )
    store = PluginStateStore.for_test(tmp_path)

    state = store.get("demo")

    assert state is not None
    assert state.enabled is False
    assert state.scope == "project"


def test_state_store_rejects_non_string_key_from_existing_state(tmp_path):
    # JSON object keys are always strings, so a numeric legacy key becomes "1"
    # and remains a valid canonical identity. Direct API misuse is rejected.
    store = PluginStateStore.for_test(tmp_path)
    with pytest.raises(ValueError, match="must be a string"):
        store.get(None)  # type: ignore[arg-type]
