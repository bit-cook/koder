"""Tests for configurable keybindings."""

import json

from koder_agent.core.keybindings import (
    DEFAULT_KEYBINDINGS,
    KeyAction,
    KeybindingManager,
    normalize_key_sequence,
)


def test_default_keybindings_exist():
    assert "submit" in DEFAULT_KEYBINDINGS
    assert "cancel" in DEFAULT_KEYBINDINGS
    assert "newline" in DEFAULT_KEYBINDINGS
    assert "exit" in DEFAULT_KEYBINDINGS
    assert "search" in DEFAULT_KEYBINDINGS


def test_key_action_enum():
    assert KeyAction.SUBMIT.value == "submit"
    assert KeyAction.CANCEL.value == "cancel"
    assert KeyAction.NEWLINE.value == "newline"
    assert KeyAction.EXIT.value == "exit"


def test_default_submit_is_enter():
    assert DEFAULT_KEYBINDINGS["submit"] == "enter"


def test_default_cancel_is_ctrl_c():
    assert DEFAULT_KEYBINDINGS["cancel"] == "c-c"


def test_manager_loads_defaults():
    mgr = KeybindingManager()
    assert mgr.get_key("submit") == "enter"
    assert mgr.get_key("cancel") == "c-c"


def test_manager_get_unknown_returns_none():
    mgr = KeybindingManager()
    assert mgr.get_key("nonexistent") is None


def test_manager_load_overrides(tmp_path):
    overrides = {"submit": "c-m", "cancel": "c-q"}
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    mgr = KeybindingManager(config_path=config_path)
    assert mgr.get_key("submit") == "c-m"  # Overridden
    assert mgr.get_key("cancel") == "c-q"  # Overridden
    assert mgr.get_key("exit") == "c-d"  # Default preserved


def test_manager_invalid_json(tmp_path):
    config_path = tmp_path / "keybindings.json"
    config_path.write_text("not json{{{")

    mgr = KeybindingManager(config_path=config_path)
    # Should fall back to defaults
    assert mgr.get_key("submit") == "enter"


def test_manager_nonexistent_file(tmp_path):
    mgr = KeybindingManager(config_path=tmp_path / "missing.json")
    assert mgr.get_key("submit") == "enter"


def test_null_unbind(tmp_path):
    """Setting a key to null should unbind it."""
    overrides = {"search": None}
    config_path = tmp_path / "keybindings.json"
    config_path.write_text(json.dumps(overrides))

    mgr = KeybindingManager(config_path=config_path)
    assert mgr.get_key("search") is None


def test_get_all_bindings():
    mgr = KeybindingManager()
    bindings = mgr.get_all_bindings()
    assert isinstance(bindings, dict)
    assert "submit" in bindings
    assert len(bindings) >= 8


def test_save_overrides(tmp_path):
    config_path = tmp_path / "keybindings.json"
    mgr = KeybindingManager(config_path=config_path)
    mgr.set_override("submit", "c-m")
    mgr.save()

    # Verify saved
    data = json.loads(config_path.read_text())
    assert data["submit"] == "c-m"


def test_normalize_key_sequence_accepts_chords_and_rejects_unknown_keys():
    assert normalize_key_sequence("escape   enter") == "escape enter"

    try:
        normalize_key_sequence("definitely-not-a-key")
    except ValueError as exc:
        assert "Invalid key" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("invalid key sequence was accepted")


def test_set_override_rejects_invalid_key_sequence(tmp_path):
    config_path = tmp_path / "keybindings.json"
    mgr = KeybindingManager(config_path=config_path)

    try:
        mgr.set_override("submit", "definitely-not-a-key")
    except ValueError as exc:
        assert "Invalid key" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("invalid key sequence was accepted")

    mgr.save()
    assert json.loads(config_path.read_text()) == {}


def test_reset_removes_override(tmp_path):
    config_path = tmp_path / "keybindings.json"
    mgr = KeybindingManager(config_path=config_path)
    mgr.set_override("submit", "c-m")
    assert mgr.get_key("submit") == "c-m"
    mgr.reset("submit")
    assert mgr.get_key("submit") == "enter"  # Back to default
