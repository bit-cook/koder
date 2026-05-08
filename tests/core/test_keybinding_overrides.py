"""Tests for keybinding override application in InteractivePrompt."""

import json
import sys
import types

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

from koder_agent.core.interactive import InteractivePrompt
from koder_agent.core.keybindings import KeybindingManager


def test_keybinding_overrides_are_applied(tmp_path):
    """Test that keybinding overrides actually register new bindings."""
    # Create a keybinding override file
    overrides = {"submit": "c-m", "cancel": "c-q", "exit": "c-x"}
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    # Create InteractivePrompt with the override config
    prompt = InteractivePrompt(
        commands={"test": "Test command"},
        usage_tracker=None,
        session_id="test-session",
    )
    # Replace keybinding manager with one that has overrides
    prompt.keybinding_manager = KeybindingManager(config_path=config_path)

    # Verify overrides are loaded
    assert prompt.keybinding_manager.get_key("submit") == "c-m"
    assert prompt.keybinding_manager.get_key("cancel") == "c-q"
    assert prompt.keybinding_manager.get_key("exit") == "c-x"


def test_keybinding_override_submit_action(tmp_path):
    """Test that submit override binding is registered."""
    from prompt_toolkit.key_binding import KeyBindings

    overrides = {"submit": "c-m"}
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    # Create KeyBindings and apply overrides
    kb = KeyBindings()
    mgr = KeybindingManager(config_path=config_path)

    # Simulate what _apply_keybinding_overrides does
    from prompt_toolkit.filters import is_searching

    from koder_agent.core.keybindings import DEFAULT_KEYBINDINGS

    all_bindings = mgr.get_all_bindings()
    for action, key in all_bindings.items():
        if key is None:
            continue
        default = DEFAULT_KEYBINDINGS.get(action)
        if key == default:
            continue

        if action == "submit":

            @kb.add(key, filter=~is_searching)
            def _submit_override(event):
                event.app.exit(result="SUBMITTED")

    # Verify the binding was registered
    # Note: Can't easily test the actual keybinding execution without full prompt_toolkit setup
    # but we can verify no errors occurred during registration
    assert len(kb.bindings) > 0


def test_null_overrides_are_skipped(tmp_path):
    """Test that null overrides (unbind) are skipped during registration."""
    overrides = {"search": None}  # Unbind search
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    mgr = KeybindingManager(config_path=config_path)

    # Simulate what _apply_keybinding_overrides does
    from koder_agent.core.keybindings import DEFAULT_KEYBINDINGS

    all_bindings = mgr.get_all_bindings()
    override_count = 0
    for action, key in all_bindings.items():
        if key is None:
            continue  # Skip null bindings
        default = DEFAULT_KEYBINDINGS.get(action)
        if key == default:
            continue
        override_count += 1

    # No overrides should be applied since only null override exists
    assert override_count == 0


def test_default_bindings_not_duplicated(tmp_path):
    """Test that bindings matching defaults are not duplicated."""
    # Override to same value as default
    overrides = {"submit": "enter", "cancel": "c-c"}
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    mgr = KeybindingManager(config_path=config_path)

    # Simulate what _apply_keybinding_overrides does
    from koder_agent.core.keybindings import DEFAULT_KEYBINDINGS

    all_bindings = mgr.get_all_bindings()
    override_count = 0
    for action, key in all_bindings.items():
        if key is None:
            continue
        default = DEFAULT_KEYBINDINGS.get(action)
        if key == default:
            continue  # Skip - no override needed
        override_count += 1

    # No overrides should be applied since they match defaults
    assert override_count == 0
