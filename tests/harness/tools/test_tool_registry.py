from koder_agent.harness.tools.registry import ToolRegistry


def test_empty_registry_has_no_tools():
    registry = ToolRegistry.empty()
    assert registry.list_names() == []


def test_register_adds_tool():
    from koder_agent.harness.tools.registry import ToolSpec

    registry = ToolRegistry.empty()
    registry.register(ToolSpec(name="test_tool", enabled=True))
    assert registry.get("test_tool") is not None
    assert registry.get("test_tool").enabled is True


def test_with_core_tools_registers_file_tools():
    registry = ToolRegistry.with_core_tools(categories={"file"})
    names = set(registry.list_names())
    assert {"read_file", "write_file", "edit_file"} <= names


def test_with_core_tools_registers_code_tools():
    registry = ToolRegistry.with_core_tools(categories={"code"})
    names = set(registry.list_names())
    assert "code_intelligence" in names
