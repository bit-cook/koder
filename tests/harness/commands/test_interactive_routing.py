from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.commands.registry import CommandRegistry


def test_harness_interactive_handler_exposes_harness_commands():
    handler = HarnessInteractiveCommandHandler()
    names = {name for name, _ in handler.get_command_list()}
    assert {
        "channels",
        "skills",
        "plugin",
        "memory",
        "tasks",
        "permissions",
        "theme",
        "mcp",
    } <= names


def test_harness_interactive_handler_exposes_all_default_registry_commands():
    registry_names = set(CommandRegistry.with_defaults().list_names())
    handler_names = {name for name, _ in HarnessInteractiveCommandHandler().get_command_list()}
    assert registry_names <= handler_names


def test_harness_interactive_handler_exposes_all_program_registry_commands():
    registry_names = set(CommandRegistry.with_all_commands().list_names())
    handler_names = {name for name, _ in HarnessInteractiveCommandHandler().get_command_list()}
    assert registry_names <= handler_names


def test_harness_interactive_handler_has_no_generic_fallbacks():
    handler = HarnessInteractiveCommandHandler()
    fallback = [name for name, fn in handler.commands.items() if fn.__name__ == "_handler"]
    assert fallback == []


def test_harness_interactive_handler_has_specific_command_descriptions():
    handler = HarnessInteractiveCommandHandler()
    missing_specs = [name for name in handler.commands if handler.registry.get(name) is None]
    generic_descriptions = [
        name
        for name, description in handler.get_command_list()
        if description.startswith("Execute /")
    ]

    assert missing_specs == []
    assert generic_descriptions == []


def test_harness_interactive_handler_recognizes_slash_input():
    handler = HarnessInteractiveCommandHandler()
    assert handler.is_slash_command("/skills") is True
    assert handler.is_slash_command("skills") is False
