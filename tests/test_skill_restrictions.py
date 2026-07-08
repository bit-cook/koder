"""Tests for skill-based tool restriction enforcement."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.tools.skill import Skill  # noqa: E402
from koder_agent.tools.skill_context import (  # noqa: E402
    SkillRestrictions,
    add_skill_restrictions,
    clear_restrictions,
    get_active_restrictions,
    has_active_restrictions,
)


@pytest.fixture(autouse=True)
def reset_restrictions():
    """Clear restrictions before and after each test."""
    clear_restrictions()
    yield
    clear_restrictions()


class TestSkillRestrictions:
    """Tests for the SkillRestrictions dataclass."""

    def test_always_allowed_tools_bypass_restrictions(self):
        """Test that always-allowed tools work regardless of restrictions."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools={"read_file"},
        )

        # Always-allowed tools should pass
        assert restrictions.is_tool_allowed("get_skill") is True
        assert restrictions.is_tool_allowed("todo_read") is True
        assert restrictions.is_tool_allowed("todo_write") is True

    def test_allowed_tools_are_permitted(self):
        """Test that tools in the allowed set are permitted."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools={"read_file", "glob_search"},
        )

        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("glob_search") is True

    def test_non_allowed_tools_are_blocked(self):
        """Test that tools not in the allowed set are blocked."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools={"read_file"},
        )

        assert restrictions.is_tool_allowed("write_file") is False
        assert restrictions.is_tool_allowed("run_shell") is False

    def test_empty_allowed_tools_permits_all(self):
        """Test that empty allowed_tools means no restrictions."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools=set(),
        )

        # Should allow any tool when no restrictions defined
        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("write_file") is True
        assert restrictions.is_tool_allowed("run_shell") is True

    def test_add_skill_accumulates_tools(self):
        """Test that adding skills accumulates allowed tools (union)."""
        restrictions = SkillRestrictions()

        restrictions.add_skill("skill1", ["read_file", "glob_search"])
        assert restrictions.allowed_tools == {"read_file", "glob_search"}
        assert restrictions.loaded_skills == ["skill1"]

        restrictions.add_skill("skill2", ["write_file", "edit_file"])
        assert restrictions.allowed_tools == {
            "read_file",
            "glob_search",
            "write_file",
            "edit_file",
        }
        assert restrictions.loaded_skills == ["skill1", "skill2"]

    def test_add_same_skill_twice_no_duplicates(self):
        """Test that adding the same skill twice doesn't create duplicates."""
        restrictions = SkillRestrictions()

        restrictions.add_skill("skill1", ["read_file"])
        restrictions.add_skill("skill1", ["write_file"])

        assert restrictions.loaded_skills == ["skill1"]
        assert restrictions.allowed_tools == {"read_file", "write_file"}


class TestSkillContextFunctions:
    """Tests for the skill context management functions."""

    def test_get_active_restrictions_returns_none_initially(self):
        """Test that no restrictions are active initially."""
        assert get_active_restrictions() is None
        assert has_active_restrictions() is False

    def test_add_skill_restrictions_activates_restrictions(self):
        """Test that adding skill restrictions activates them."""
        skill = Skill(
            name="test-skill",
            description="Test skill",
            content="Content",
            allowed_tools=["read_file", "glob_search"],
        )

        add_skill_restrictions(skill)

        restrictions = get_active_restrictions()
        assert restrictions is not None
        assert has_active_restrictions() is True
        assert "test-skill" in restrictions.loaded_skills
        assert restrictions.allowed_tools == {"read_file", "glob_search"}

    def test_add_skill_without_allowed_tools_does_nothing(self):
        """Test that adding a skill without allowed_tools doesn't create restrictions."""
        skill = Skill(
            name="unrestricted-skill",
            description="No restrictions",
            content="Content",
            allowed_tools=None,
        )

        add_skill_restrictions(skill)

        assert get_active_restrictions() is None
        assert has_active_restrictions() is False

    def test_clear_restrictions_removes_all(self):
        """Test that clear_restrictions removes all active restrictions."""
        skill = Skill(
            name="test-skill",
            description="Test skill",
            content="Content",
            allowed_tools=["read_file"],
        )

        add_skill_restrictions(skill)
        assert has_active_restrictions() is True

        clear_restrictions()
        assert get_active_restrictions() is None
        assert has_active_restrictions() is False

    def test_multiple_skills_union_behavior(self):
        """Test that multiple skills with restrictions combine (union)."""
        skill1 = Skill(
            name="skill1",
            description="First skill",
            content="Content",
            allowed_tools=["read_file", "glob_search"],
        )
        skill2 = Skill(
            name="skill2",
            description="Second skill",
            content="Content",
            allowed_tools=["write_file", "edit_file"],
        )

        add_skill_restrictions(skill1)
        add_skill_restrictions(skill2)

        restrictions = get_active_restrictions()
        assert restrictions is not None
        assert restrictions.loaded_skills == ["skill1", "skill2"]
        assert restrictions.allowed_tools == {
            "read_file",
            "glob_search",
            "write_file",
            "edit_file",
        }

        # All tools from both skills should be allowed
        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("write_file") is True
        # Tools not in either skill should be blocked
        assert restrictions.is_tool_allowed("run_shell") is False


class TestGetSkillDoesNotSelfClearRestrictions:
    """Tests for the corrected, safer get_skill restriction semantics (S2).

    Previously, calling get_skill on a skill with no `allowed_tools` invoked
    clear_restrictions(), wiping the restrictions of a previously-loaded
    restricted skill. That let the model escape its own sandbox by loading any
    benign, unrestricted skill. The corrected semantics: loading an
    unrestricted skill is a no-op for restrictions (only loading a restricted
    skill adds its allowed_tools). clear_restrictions() remains an explicit API.
    """

    # NOTE: We test the restriction-application logic via the module-level
    # _apply_skill_restrictions helper that get_skill calls. The get_skill
    # FunctionTool wrapper runs in an isolated copied context (an SDK
    # implementation detail), so contextvar mutations made inside on_invoke_tool
    # are not observable from the caller's context -- testing through the wrapper
    # would only exercise the SDK, not the S2 logic. The helper is the exact
    # code path get_skill executes, so this faithfully covers the fix.

    def test_loading_unrestricted_skill_does_not_clear_restrictions(self):
        """An unrestricted skill loaded after a restricted one must NOT clear it."""
        from koder_agent.tools.skill import _apply_skill_restrictions

        restricted = Skill(
            name="restricted-skill",
            description="Restricted",
            content="Content",
            allowed_tools=["read_file"],
        )
        unrestricted = Skill(
            name="unrestricted-skill",
            description="No restrictions",
            content="Content",
            allowed_tools=None,
        )

        # Load the restricted skill -> restrictions become active.
        _apply_skill_restrictions(restricted)
        assert has_active_restrictions() is True
        assert get_active_restrictions().allowed_tools == {"read_file"}

        # Load an unrestricted skill -> restrictions must be PRESERVED (no-op).
        _apply_skill_restrictions(unrestricted)
        assert has_active_restrictions() is True
        restrictions = get_active_restrictions()
        assert restrictions is not None
        assert restrictions.allowed_tools == {"read_file"}
        assert "restricted-skill" in restrictions.loaded_skills

    def test_loading_unrestricted_skill_with_no_active_restrictions_stays_unrestricted(self):
        """Loading an unrestricted skill when nothing is active leaves no restrictions."""
        from koder_agent.tools.skill import _apply_skill_restrictions

        unrestricted = Skill(
            name="unrestricted-skill",
            description="No restrictions",
            content="Content",
            allowed_tools=None,
        )

        _apply_skill_restrictions(unrestricted)
        assert get_active_restrictions() is None
        assert has_active_restrictions() is False

    def test_loading_second_restricted_skill_unions(self):
        """Loading two restricted skills unions their tools (accumulate, not clear)."""
        from koder_agent.tools.skill import _apply_skill_restrictions

        skill1 = Skill(
            name="skill1",
            description="First",
            content="Content",
            allowed_tools=["read_file"],
        )
        skill2 = Skill(
            name="skill2",
            description="Second",
            content="Content",
            allowed_tools=["write_file"],
        )

        _apply_skill_restrictions(skill1)
        _apply_skill_restrictions(skill2)

        restrictions = get_active_restrictions()
        assert restrictions is not None
        assert restrictions.allowed_tools == {"read_file", "write_file"}
        assert restrictions.loaded_skills == ["skill1", "skill2"]

    def test_unrestricted_skill_between_two_restricted_preserves_union(self):
        """An unrestricted skill loaded between restricted ones must not wipe state."""
        from koder_agent.tools.skill import _apply_skill_restrictions

        skill1 = Skill(name="skill1", description="d", content="c", allowed_tools=["read_file"])
        benign = Skill(name="benign", description="d", content="c", allowed_tools=None)
        skill2 = Skill(name="skill2", description="d", content="c", allowed_tools=["write_file"])

        _apply_skill_restrictions(skill1)
        _apply_skill_restrictions(benign)  # must be a no-op, not a clear
        _apply_skill_restrictions(skill2)

        restrictions = get_active_restrictions()
        assert restrictions is not None
        assert restrictions.allowed_tools == {"read_file", "write_file"}
        assert restrictions.loaded_skills == ["skill1", "skill2"]


class TestSkillGuardrail:
    """Tests for the skill tool restriction guardrail."""

    def test_guardrail_allows_when_no_restrictions(self):
        """Test that guardrail allows all tools when no restrictions active."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Create mock data
        mock_context = MagicMock()
        mock_context.tool_name = "run_shell"
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "allow"

    def test_guardrail_allows_permitted_tools(self):
        """Test that guardrail allows tools in the allowed set."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions
        skill = Skill(
            name="read-only-skill",
            description="Read only",
            content="Content",
            allowed_tools=["read_file", "glob_search"],
        )
        add_skill_restrictions(skill)

        # Create mock data
        mock_context = MagicMock()
        mock_context.tool_name = "read_file"
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "allow"

    def test_guardrail_blocks_unpermitted_tools(self):
        """Test that guardrail blocks tools not in the allowed set."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions
        skill = Skill(
            name="read-only-skill",
            description="Read only",
            content="Content",
            allowed_tools=["read_file"],
        )
        add_skill_restrictions(skill)

        # Create mock data for a blocked tool
        mock_context = MagicMock()
        mock_context.tool_name = "write_file"
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "reject_content"
        assert result.output_info.get("blocked_tool") == "write_file"

    def test_guardrail_always_allows_escape_tools(self):
        """Test that always-allowed tools work even with restrictions."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions
        skill = Skill(
            name="restrictive-skill",
            description="Very restrictive",
            content="Content",
            allowed_tools=["read_file"],  # Only read_file allowed
        )
        add_skill_restrictions(skill)

        # get_skill should still work (escape hatch)
        mock_context = MagicMock()
        mock_context.tool_name = "get_skill"
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "allow"

    def test_guardrail_rejects_missing_tool_name(self):
        """Test that missing tool_name is handled gracefully and rejected."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions
        skill = Skill(
            name="restrictive-skill",
            description="Very restrictive",
            content="Content",
            allowed_tools=["read_file"],
        )
        add_skill_restrictions(skill)

        # Mock context without tool_name attribute
        mock_context = MagicMock(spec=[])  # Empty spec - no attributes
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "reject_content"
        assert result.output_info.get("error") == "missing_tool_name"

    def test_guardrail_rejects_empty_tool_name(self):
        """Test that empty tool_name string is handled gracefully and rejected."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions
        skill = Skill(
            name="restrictive-skill",
            description="Very restrictive",
            content="Content",
            allowed_tools=["read_file"],
        )
        add_skill_restrictions(skill)

        # Mock context with empty tool_name
        mock_context = MagicMock()
        mock_context.tool_name = ""
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)

        assert result.behavior["type"] == "reject_content"
        assert result.output_info.get("error") == "missing_tool_name"


class TestToolGuardrailIntegration:
    """Tests for proper guardrail integration with tools and agent.

    These tests ensure that:
    1. ToolInputGuardrail is attached to tools (not agent's input_guardrails)
    2. Agent creation works without AttributeError for run_in_parallel
    3. The guardrail type is correct for the SDK's expectations
    """

    def test_all_tools_have_skill_guardrail_attached(self):
        """Test that get_all_tools() attaches skill_restriction_guardrail to each tool."""
        from agents import FunctionTool

        from koder_agent.agentic.skill_guardrail import skill_restriction_guardrail
        from koder_agent.tools import get_all_tools

        tools = get_all_tools()

        # Verify we have tools
        assert len(tools) > 0, "Expected at least one tool"

        # Verify each FunctionTool has the guardrail attached
        for tool in tools:
            if isinstance(tool, FunctionTool):
                assert tool.tool_input_guardrails is not None, (
                    f"Tool '{tool.name}' should have tool_input_guardrails"
                )
                assert len(tool.tool_input_guardrails) > 0, (
                    f"Tool '{tool.name}' should have at least one guardrail"
                )
                assert skill_restriction_guardrail in tool.tool_input_guardrails, (
                    f"Tool '{tool.name}' should have skill_restriction_guardrail"
                )

    def test_skill_restriction_guardrail_is_correct_type(self):
        """Test that skill_restriction_guardrail is a ToolInputGuardrail, not InputGuardrail."""
        from agents import ToolInputGuardrail

        from koder_agent.agentic.skill_guardrail import skill_restriction_guardrail

        # The guardrail must be ToolInputGuardrail (for per-tool validation)
        # NOT InputGuardrail (which has run_in_parallel and is for agent-level)
        assert isinstance(skill_restriction_guardrail, ToolInputGuardrail), (
            "skill_restriction_guardrail must be a ToolInputGuardrail instance"
        )

        # ToolInputGuardrail should NOT have run_in_parallel attribute
        # (that's only on InputGuardrail for agent-level guardrails)
        assert not hasattr(skill_restriction_guardrail, "run_in_parallel"), (
            "ToolInputGuardrail should not have run_in_parallel attribute"
        )

    def test_agent_creation_with_tools_no_attribute_error(self):
        """Test that Agent can be created with tools without run_in_parallel AttributeError.

        This is a regression test for the bug where ToolInputGuardrail was incorrectly
        passed to Agent's input_guardrails (which expects InputGuardrail with run_in_parallel).
        """
        from agents import Agent

        from koder_agent.tools import get_all_tools

        tools = get_all_tools()

        # This should NOT raise AttributeError: 'ToolInputGuardrail' has no attribute 'run_in_parallel'
        agent = Agent(
            name="test-agent",
            instructions="Test agent for guardrail integration",
            tools=tools,
        )

        assert agent is not None
        assert len(agent.tools) == len(tools)
        # Agent should NOT have input_guardrails set (guardrails are on tools now)
        assert len(agent.input_guardrails) == 0

    def test_tool_guardrails_not_duplicated_on_repeated_calls(self):
        """Test that calling get_all_tools() multiple times doesn't duplicate guardrails."""
        from koder_agent.agentic.skill_guardrail import skill_restriction_guardrail
        from koder_agent.tools import get_all_tools

        # Call get_all_tools multiple times to simulate repeated usage
        get_all_tools()
        get_all_tools()
        tools = get_all_tools()

        # Check that guardrails aren't duplicated
        for tool in tools:
            if hasattr(tool, "tool_input_guardrails") and tool.tool_input_guardrails:
                guardrail_count = tool.tool_input_guardrails.count(skill_restriction_guardrail)
                assert guardrail_count == 1, (
                    f"Tool '{tool.name}' has {guardrail_count} copies of skill_restriction_guardrail, expected 1"
                )


class TestPatternBasedRestrictions:
    """Tests for pattern-based tool restriction matching.

    Pattern syntax:
    - "read_file"           - Exact tool name match
    - "run_shell:git *"     - Shell commands matching glob pattern
    - "run_shell:*"         - All shell commands allowed
    - "*"                   - Wildcard, all tools allowed
    """

    def test_exact_tool_name_match(self):
        """Test that exact tool names still work."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools={"read_file", "write_file"},
        )

        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("write_file") is True
        assert restrictions.is_tool_allowed("run_shell") is False

    def test_wildcard_allows_all_tools(self):
        """Test that '*' pattern allows all tools."""
        restrictions = SkillRestrictions(
            loaded_skills=["permissive-skill"],
            allowed_tools={"*"},
        )

        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("write_file") is True
        assert restrictions.is_tool_allowed("run_shell") is True
        assert restrictions.is_tool_allowed("any_tool_name") is True

    def test_shell_command_pattern_allows_matching_commands(self):
        """Test that 'run_shell:pattern' allows matching shell commands."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        # Should allow git commands
        git_status_args = json.dumps({"command": "git status"})
        assert restrictions.is_tool_allowed("run_shell", git_status_args) is True

        git_commit_args = json.dumps({"command": "git commit -m 'test'"})
        assert restrictions.is_tool_allowed("run_shell", git_commit_args) is True

        # Should block non-git commands
        cat_args = json.dumps({"command": "cat /etc/passwd"})
        assert restrictions.is_tool_allowed("run_shell", cat_args) is False

        rm_args = json.dumps({"command": "rm -rf /"})
        assert restrictions.is_tool_allowed("run_shell", rm_args) is False

    def test_shell_command_pattern_with_wildcard(self):
        """Test that 'run_shell:*' allows all shell commands."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["shell-skill"],
            allowed_tools={"run_shell:*"},
        )

        # Should allow any command
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "cat foo.txt"}))
            is True
        )
        assert restrictions.is_tool_allowed("run_shell", json.dumps({"command": "ls -la"})) is True
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "rm -rf /"})) is True
        )

    def test_shell_pattern_blocks_run_shell_without_args(self):
        """Test that shell patterns require tool_args to match."""
        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        # Without tool_args, pattern can't match
        assert restrictions.is_tool_allowed("run_shell") is False
        assert restrictions.is_tool_allowed("run_shell", None) is False
        assert restrictions.is_tool_allowed("run_shell", "") is False

    def test_multiple_shell_patterns(self):
        """Test multiple shell command patterns work together."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["dev-skill"],
            allowed_tools={"run_shell:git *", "run_shell:npm *", "run_shell:cat *"},
        )

        # All patterns should work
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "git status"})) is True
        )
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "npm install"}))
            is True
        )
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "cat README.md"}))
            is True
        )

        # Non-matching commands should be blocked
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "rm -rf /"})) is False
        )

    def test_git_command_pattern_matching(self):
        """Test that 'git_command:pattern' matches git command args."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-readonly"],
            allowed_tools={"git_command:status", "git_command:log *", "git_command:diff *"},
        )

        # Exact match
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "status"})) is True
        )

        # Pattern match
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "log --oneline"}))
            is True
        )
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "diff HEAD~1"}))
            is True
        )

        # Non-matching
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "push origin main"}))
            is False
        )
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "commit -m 'test'"}))
            is False
        )

    def test_mixed_exact_and_pattern_restrictions(self):
        """Test combining exact tool names with patterns."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["mixed-skill"],
            allowed_tools={"read_file", "glob_search", "run_shell:cat *"},
        )

        # Exact matches work
        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("glob_search") is True

        # Pattern matches work
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "cat foo.txt"}))
            is True
        )

        # Non-allowed tools blocked
        assert restrictions.is_tool_allowed("write_file") is False
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "rm foo.txt"}))
            is False
        )

    def test_glob_pattern_on_tool_names(self):
        """Test glob patterns on tool names themselves."""
        restrictions = SkillRestrictions(
            loaded_skills=["file-skill"],
            allowed_tools={"*_file", "glob_*"},
        )

        # Matching patterns
        assert restrictions.is_tool_allowed("read_file") is True
        assert restrictions.is_tool_allowed("write_file") is True
        assert restrictions.is_tool_allowed("edit_file") is True
        assert restrictions.is_tool_allowed("glob_search") is True

        # Non-matching
        assert restrictions.is_tool_allowed("run_shell") is False
        assert restrictions.is_tool_allowed("web_search") is False

    def test_invalid_json_in_tool_args_is_safe(self):
        """Test that invalid JSON in tool_args doesn't crash."""
        restrictions = SkillRestrictions(
            loaded_skills=["test-skill"],
            allowed_tools={"run_shell:git *"},
        )

        # Invalid JSON should not match (but also not crash)
        assert restrictions.is_tool_allowed("run_shell", "not valid json") is False
        assert restrictions.is_tool_allowed("run_shell", "{broken") is False
        assert restrictions.is_tool_allowed("run_shell", "null") is False

    def test_always_allowed_tools_bypass_patterns(self):
        """Test that always-allowed tools work even with restrictive patterns."""
        restrictions = SkillRestrictions(
            loaded_skills=["restrictive-skill"],
            allowed_tools={"read_file"},  # Very restrictive
        )

        # Always-allowed should still work
        assert restrictions.is_tool_allowed("get_skill") is True
        assert restrictions.is_tool_allowed("todo_read") is True
        assert restrictions.is_tool_allowed("todo_write") is True


class TestPatternGuardrailIntegration:
    """Tests for pattern matching through the guardrail."""

    def test_guardrail_with_shell_pattern(self):
        """Test that guardrail correctly enforces shell command patterns."""
        import json

        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions with shell pattern
        skill = Skill(
            name="git-only-skill",
            description="Only allows git commands",
            content="Content",
            allowed_tools=["run_shell:git *", "read_file"],
        )
        add_skill_restrictions(skill)

        # Test allowed git command
        mock_context = MagicMock()
        mock_context.tool_name = "run_shell"
        mock_context.tool_arguments = json.dumps({"command": "git status"})
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)
        assert result.behavior["type"] == "allow"

        # Test blocked command
        mock_context.tool_arguments = json.dumps({"command": "rm -rf /"})
        result = skill_tool_restriction_guardrail(data)
        assert result.behavior["type"] == "reject_content"

    def test_guardrail_with_wildcard_pattern(self):
        """Test that guardrail correctly handles wildcard pattern."""
        from agents import ToolInputGuardrailData

        from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail

        # Set up restrictions with wildcard
        skill = Skill(
            name="permissive-skill",
            description="Allows everything",
            content="Content",
            allowed_tools=["*"],
        )
        add_skill_restrictions(skill)

        # Any tool should be allowed
        mock_context = MagicMock()
        mock_context.tool_name = "any_tool"
        mock_context.tool_arguments = None
        data = MagicMock(spec=ToolInputGuardrailData)
        data.context = mock_context

        result = skill_tool_restriction_guardrail(data)
        assert result.behavior["type"] == "allow"

        mock_context.tool_name = "another_tool"
        result = skill_tool_restriction_guardrail(data)
        assert result.behavior["type"] == "allow"


class TestShellPatternChainingBypass:
    """Regression tests for the command-chaining restriction bypass (S2).

    ``fnmatch(command, "git *")`` matched ``git status; rm -rf /`` because the
    whole string still starts with ``git ``. The matcher now splits on shell
    operators and requires EVERY segment to match, and rejects command/process
    substitution outright.
    """

    def test_semicolon_chaining_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        args = json.dumps({"command": "git status; rm -rf /"})
        assert restrictions.is_tool_allowed("run_shell", args) is False

    def test_and_chaining_with_pipe_to_interpreter_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        args = json.dumps({"command": "git log && curl x|sh"})
        assert restrictions.is_tool_allowed("run_shell", args) is False

    def test_pipe_to_shell_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        args = json.dumps({"command": "git diff | sh"})
        assert restrictions.is_tool_allowed("run_shell", args) is False

    def test_or_chaining_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        args = json.dumps({"command": "git status || rm -rf ~"})
        assert restrictions.is_tool_allowed("run_shell", args) is False

    def test_command_substitution_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        # Substitution smuggles an arbitrary command inside an otherwise
        # git-shaped line; a first-token pattern cannot police it.
        assert (
            restrictions.is_tool_allowed(
                "run_shell", json.dumps({"command": "git log $(rm -rf /)"})
            )
            is False
        )
        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "git log `rm -rf /`"}))
            is False
        )

    def test_plain_command_still_allowed(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-skill"],
            allowed_tools={"run_shell:git *"},
        )

        assert (
            restrictions.is_tool_allowed("run_shell", json.dumps({"command": "git status"})) is True
        )
        assert (
            restrictions.is_tool_allowed(
                "run_shell", json.dumps({"command": "git commit -m 'test'"})
            )
            is True
        )

    def test_quoted_operator_is_not_a_separator(self):
        import json

        # A pipe inside a quoted grep pattern must NOT be treated as a chain.
        restrictions = SkillRestrictions(
            loaded_skills=["grep-skill"],
            allowed_tools={"run_shell:grep *"},
        )

        args = json.dumps({"command": "grep 'a|b' file.txt"})
        assert restrictions.is_tool_allowed("run_shell", args) is True

    def test_git_command_chaining_is_rejected(self):
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-readonly"],
            allowed_tools={"git_command:status*"},
        )

        # Plain read-only still works.
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "status"})) is True
        )

        # Chained / substituted git args are rejected.
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "status; rm -rf /"}))
            is False
        )
        assert (
            restrictions.is_tool_allowed(
                "git_command", json.dumps({"command": "status $(rm -rf /)"})
            )
            is False
        )


# ---------------------------------------------------------------------------
# H8: Malformed frontmatter must reject skill (fail-closed)
# ---------------------------------------------------------------------------


class TestMalformedFrontmatterRejectsSkill:
    def test_malformed_yaml_returns_none(self, tmp_path):
        """A skill with invalid YAML frontmatter must not be loaded."""
        from koder_agent.tools.skill import SkillLoader

        skill_path = tmp_path / "bad-skill.md"
        skill_path.write_text(
            "---\n"
            "name: bad-skill\n"
            "allowed_tools: [read_file\n"  # Invalid YAML (missing closing bracket)
            "---\n"
            "# Skill content\n"
            "Do something dangerous\n",
            encoding="utf-8",
        )

        loader = SkillLoader(tmp_path)
        result = loader.load_skill(skill_path, source="project")

        # Must return None — skill should NOT be loaded
        assert result is None

    def test_non_mapping_frontmatter_returns_none(self, tmp_path):
        """A skill with YAML that parses to a non-dict must not be loaded."""
        from koder_agent.tools.skill import SkillLoader

        skill_path = tmp_path / "list-skill.md"
        skill_path.write_text(
            "---\n- item1\n- item2\n---\n# Skill content\n",
            encoding="utf-8",
        )

        loader = SkillLoader(tmp_path)
        result = loader.load_skill(skill_path, source="project")

        # Must return None — non-mapping frontmatter is invalid
        assert result is None


# ---------------------------------------------------------------------------
# H9: disable_model_invocation blocks get_skill
# ---------------------------------------------------------------------------


class TestDisableModelInvocationBlocksGetSkill:
    def test_get_skill_rejects_disabled_skill(self, monkeypatch):
        """get_skill must refuse to load a skill with disable_model_invocation=True."""
        import asyncio
        import json as json_mod

        from koder_agent.tools.skill import Skill, get_skill

        disabled_skill = Skill(
            name="secret-skill",
            description="A secret skill",
            content="Secret instructions",
            disable_model_invocation=True,
        )

        # Mock _get_merged_skills to return our test skill
        monkeypatch.setattr(
            "koder_agent.tools.skill._get_merged_skills",
            lambda: {"secret-skill": disabled_skill},
        )

        # Invoke via the on_invoke_tool async API (ctx=None supported by wrapper)
        result = asyncio.run(
            get_skill.on_invoke_tool(None, json_mod.dumps({"skill_name": "secret-skill"}))
        )

        assert "cannot be loaded by the model" in result
        assert "disable_model_invocation" in result

    def test_get_skill_allows_normal_skill(self, monkeypatch):
        """get_skill should load a skill without disable_model_invocation."""
        import asyncio
        import json as json_mod

        from koder_agent.tools.skill import Skill, get_skill
        from koder_agent.tools.skill_context import clear_restrictions

        normal_skill = Skill(
            name="normal-skill",
            description="A normal skill",
            content="Normal instructions",
            disable_model_invocation=False,
        )

        monkeypatch.setattr(
            "koder_agent.tools.skill._get_merged_skills",
            lambda: {"normal-skill": normal_skill},
        )

        result = asyncio.run(
            get_skill.on_invoke_tool(None, json_mod.dumps({"skill_name": "normal-skill"}))
        )
        clear_restrictions()

        # Should return the prompt content, not an error
        assert "cannot be loaded" not in result
        assert "Normal instructions" in result


# ---------------------------------------------------------------------------
# H10: git_command restriction uses "command" field (not "args")
# ---------------------------------------------------------------------------


class TestGitCommandRestrictionUsesCommandField:
    def test_git_command_restriction_matches_command_field(self):
        """git_command tool restriction must check the 'command' field, not 'args'."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-readonly"],
            allowed_tools={"git_command:status"},
        )

        # The correct field name is "command" (matching the tool's parameter)
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "status"})) is True
        )

        # The old (buggy) field name "args" should NOT match
        assert restrictions.is_tool_allowed("git_command", json.dumps({"args": "status"})) is False

    def test_git_command_restriction_blocks_unallowed_via_command_field(self):
        """git_command restriction correctly blocks commands not in allowed list."""
        import json

        restrictions = SkillRestrictions(
            loaded_skills=["git-readonly"],
            allowed_tools={"git_command:status", "git_command:log *"},
        )

        # Allowed
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "status"})) is True
        )
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "log --oneline"}))
            is True
        )

        # Blocked
        assert (
            restrictions.is_tool_allowed("git_command", json.dumps({"command": "push origin main"}))
            is False
        )
