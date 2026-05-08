"""Tests for multi-source permission rule hierarchy."""

from koder_agent.harness.permissions.rule_sources import (
    SOURCE_PRIORITY,
    RuleHierarchy,
)


def test_source_priority_order():
    """Higher-priority sources should override lower ones."""
    assert SOURCE_PRIORITY.index("policy") < SOURCE_PRIORITY.index("user")
    assert SOURCE_PRIORITY.index("project") < SOURCE_PRIORITY.index("user")
    assert SOURCE_PRIORITY.index("user") < SOURCE_PRIORITY.index("session")


def test_all_sources_defined():
    expected = ["policy", "project", "local", "user", "cli", "command", "session"]
    for source in expected:
        assert source in SOURCE_PRIORITY


def test_add_rule_to_source():
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git *", source="project")
    rules = hierarchy.get_effective_rules()
    assert "git *" in rules.get("run_shell", {}).get("allow", [])


def test_higher_priority_wins():
    """Policy deny should override user allow."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "rm -rf *", source="user")
    hierarchy.add_rule("run_shell", "deny", "rm -rf *", source="policy")

    rules = hierarchy.get_effective_rules()
    # Policy deny should be present
    assert "rm -rf *" in rules.get("run_shell", {}).get("deny", [])


def test_merge_non_conflicting():
    """Rules from different sources that don't conflict should all appear."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git *", source="project")
    hierarchy.add_rule("run_shell", "allow", "npm *", source="user")

    rules = hierarchy.get_effective_rules()
    allow_rules = rules.get("run_shell", {}).get("allow", [])
    assert "git *" in allow_rules
    assert "npm *" in allow_rules


def test_remove_rule():
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git *", source="project")
    hierarchy.remove_rule("run_shell", "allow", "git *", source="project")

    rules = hierarchy.get_effective_rules()
    allow_rules = rules.get("run_shell", {}).get("allow", [])
    assert "git *" not in allow_rules


def test_load_from_settings_dict():
    """Should load rules from a settings dict structure."""
    settings = {
        "permissions": {
            "allow": ["Bash(git *)"],
            "deny": ["Bash(rm -rf *)"],
        }
    }
    hierarchy = RuleHierarchy()
    hierarchy.load_from_settings(settings, source="project")

    rules = hierarchy.get_effective_rules()
    assert any("git" in r for r in rules.get("Bash", {}).get("allow", []))


def test_get_rules_for_tool():
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git *", source="project")
    hierarchy.add_rule("run_shell", "deny", "rm *", source="policy")
    hierarchy.add_rule("read_file", "allow", "*.py", source="user")

    shell_rules = hierarchy.get_rules_for_tool("run_shell")
    assert "allow" in shell_rules
    assert "deny" in shell_rules

    file_rules = hierarchy.get_rules_for_tool("read_file")
    assert "allow" in file_rules


def test_empty_hierarchy():
    hierarchy = RuleHierarchy()
    assert hierarchy.get_effective_rules() == {}


def test_export_rules():
    """export_rules should return a deep copy."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git *", source="project")

    exported = hierarchy.export_rules()
    exported["run_shell"]["allow"].append("hacked")

    # Original should not be affected
    original = hierarchy.get_effective_rules()
    assert "hacked" not in original.get("run_shell", {}).get("allow", [])
