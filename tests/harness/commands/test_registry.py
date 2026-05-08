from koder_agent.harness.commands.registry import CommandRegistry


def test_default_registry_contains_core_commands():
    registry = CommandRegistry.with_defaults()
    names = set(registry.list_names())
    assert "help" in names
    assert "config" in names
    assert "model" in names
    assert "session" in names
    assert "plugins" in names or "plugin" in names


def test_help_command_has_alias():
    registry = CommandRegistry.with_defaults()
    assert registry.get("help") is not None
    assert registry.get("help").help_text is not None
    alias = registry.get("?")
    assert alias is not None
    assert alias.name == "help"


def test_all_commands_registry_includes_debug_commands():
    registry = CommandRegistry.with_all_commands()
    names = set(registry.list_names())
    assert "version" in names
    assert "env" in names
    # debug commands are only in with_all_commands, not with_defaults
    defaults = set(CommandRegistry.with_defaults().list_names())
    assert "version" not in defaults
