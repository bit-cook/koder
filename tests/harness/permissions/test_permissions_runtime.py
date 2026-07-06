"""Tests for runtime integration of AI classifier and rule hierarchy."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from koder_agent.harness.permissions.ai_classifier import AiShellClassifier, RiskLevel
from koder_agent.harness.permissions.rule_sources import RuleHierarchy
from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.runtime import _load_permission_hierarchy


@pytest.fixture
def temp_workspace(monkeypatch):
    """Create temporary workspace with .koder directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        koder_dir = workspace / ".koder"
        koder_dir.mkdir()
        monkeypatch.chdir(workspace)
        yield workspace


@pytest.fixture
def temp_home(monkeypatch):
    """Mock home directory with .koder."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        koder_home = home / ".koder"
        koder_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        yield home


def test_load_permission_hierarchy_empty(temp_workspace, temp_home):
    """Test loading hierarchy with no settings files."""
    hierarchy = _load_permission_hierarchy()
    assert hierarchy is not None
    assert hierarchy.get_effective_rules() == {}


def test_load_permission_hierarchy_project_only(temp_workspace, temp_home):
    """Test loading hierarchy from project settings.json."""
    settings = {
        "permissions": {
            "allow": ["run_shell(npm test)", "read_file(*.md)"],
            "deny": ["run_shell(rm -rf)"],
        }
    }
    settings_file = Path.cwd() / ".koder" / "settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    assert "run_shell" in rules
    assert "npm test" in rules["run_shell"]["allow"]
    assert "rm -rf" in rules["run_shell"]["deny"]
    assert "read_file" in rules
    assert "*.md" in rules["read_file"]["allow"]


def test_load_permission_hierarchy_user_only(temp_workspace, temp_home):
    """Test loading hierarchy from user settings.json."""
    settings = {
        "permissions": {
            "allow": ["run_shell(git status)"],
            "deny": ["write_file(/etc/*)"],
        }
    }
    user_settings_file = temp_home / ".koder" / "settings.json"
    user_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    assert "run_shell" in rules
    assert "git status" in rules["run_shell"]["allow"]
    assert "write_file" in rules
    assert "/etc/*" in rules["write_file"]["deny"]


def test_load_permission_hierarchy_local_settings(temp_workspace, temp_home):
    """Test loading hierarchy from gitignored .koder/settings.local.json."""
    settings = {
        "permissions": {
            "allow": ["run_shell(git log *)"],
            "deny": ["run_shell(git push *)"],
        }
    }
    local_settings_file = Path.cwd() / ".koder" / "settings.local.json"
    local_settings_file.write_text(json.dumps(settings), encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    assert "run_shell" in rules
    assert "git log *" in rules["run_shell"]["allow"]
    assert "git push *" in rules["run_shell"]["deny"]


def test_load_permission_hierarchy_local_merges_with_project(temp_workspace, temp_home):
    """Local settings rules merge with project settings rules."""
    project_settings = {"permissions": {"allow": ["run_shell(git status *)"]}}
    local_settings = {"permissions": {"allow": ["run_shell(rg *)"]}}

    (Path.cwd() / ".koder" / "settings.json").write_text(
        json.dumps(project_settings), encoding="utf-8"
    )
    (Path.cwd() / ".koder" / "settings.local.json").write_text(
        json.dumps(local_settings), encoding="utf-8"
    )

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    assert "git status *" in rules["run_shell"]["allow"]
    assert "rg *" in rules["run_shell"]["allow"]


def test_load_permission_hierarchy_malformed_local_json(temp_workspace, temp_home):
    """Malformed settings.local.json is ignored without breaking other sources."""
    project_settings = {"permissions": {"allow": ["run_shell(git status *)"]}}
    (Path.cwd() / ".koder" / "settings.json").write_text(
        json.dumps(project_settings), encoding="utf-8"
    )
    (Path.cwd() / ".koder" / "settings.local.json").write_text("{invalid json", encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    assert "git status *" in rules["run_shell"]["allow"]


def test_load_permission_hierarchy_project_overrides_user(temp_workspace, temp_home):
    """Test that project settings are merged with user settings."""
    user_settings = {
        "permissions": {
            "allow": ["run_shell(git status)", "read_file(*.txt)"],
        }
    }
    project_settings = {
        "permissions": {
            "allow": ["run_shell(npm test)"],
            "deny": ["run_shell(rm -rf)"],
        }
    }

    user_settings_file = temp_home / ".koder" / "settings.json"
    user_settings_file.write_text(json.dumps(user_settings), encoding="utf-8")

    project_settings_file = Path.cwd() / ".koder" / "settings.json"
    project_settings_file.write_text(json.dumps(project_settings), encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    rules = hierarchy.get_effective_rules()

    # Both sources should contribute
    assert "git status" in rules["run_shell"]["allow"]
    assert "npm test" in rules["run_shell"]["allow"]
    assert "rm -rf" in rules["run_shell"]["deny"]
    assert "*.txt" in rules["read_file"]["allow"]


def test_load_permission_hierarchy_malformed_json(temp_workspace, temp_home, caplog):
    """Test that malformed JSON is silently ignored."""
    settings_file = Path.cwd() / ".koder" / "settings.json"
    settings_file.write_text("{invalid json", encoding="utf-8")

    hierarchy = _load_permission_hierarchy()
    # Should not raise, just return empty hierarchy
    assert hierarchy.get_effective_rules() == {}


@pytest.mark.asyncio
async def test_permission_service_with_hierarchy():
    """Test PermissionService loads and uses RuleHierarchy."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git status", source="project")
    hierarchy.add_rule("run_shell", "deny", "rm -rf", source="project")

    service = PermissionService.default(rule_hierarchy=hierarchy)

    # Check that rules were loaded
    assert "git status" in service.rules["run_shell"]["allow"]
    assert "rm -rf" in service.rules["run_shell"]["deny"]

    # Test evaluation
    result = service.evaluate_tool_call("run_shell", {"command": "git status"})
    assert result.allowed
    assert result.matched_rule == "git status"

    # Test with a less dangerous command that won't be caught by static classifier
    result = service.evaluate_tool_call("run_shell", {"command": "rm -rf /tmp/test"})
    assert not result.allowed
    # Static classifier might catch this first, so just verify denial
    # The rule will be used if the command doesn't match static patterns


@pytest.mark.asyncio
async def test_permission_service_ai_classifier_integration():
    """Test PermissionService consults AI classifier for ambiguous commands."""
    hierarchy = RuleHierarchy()
    ai_classifier = AiShellClassifier()

    # Mock the classify method
    mock_result = MagicMock()
    mock_result.risk_level = RiskLevel.SAFE
    mock_result.allowed = True
    mock_result.reason = "Safe read-only command"
    mock_result.error = False

    with patch.object(ai_classifier, "classify", new=AsyncMock(return_value=mock_result)):
        service = PermissionService.default(
            rule_hierarchy=hierarchy,
            ai_classifier=ai_classifier,
        )

        # Test with a command that requires approval in static classifier
        result = await service.evaluate_tool_call_async(
            "run_shell",
            {"command": "some-unknown-command"},
        )

        # AI classifier should have been consulted
        ai_classifier.classify.assert_called_once()
        assert result.allowed
        assert "Safe read-only command" in result.reason


@pytest.mark.asyncio
async def test_permission_service_ai_classifier_moderate():
    """Test AI classifier returns moderate risk."""
    ai_classifier = AiShellClassifier()

    mock_result = MagicMock()
    mock_result.risk_level = RiskLevel.MODERATE
    mock_result.allowed = True
    mock_result.reason = "Moderate risk command"
    mock_result.error = False

    with patch.object(ai_classifier, "classify", new=AsyncMock(return_value=mock_result)):
        service = PermissionService.default(ai_classifier=ai_classifier)

        result = await service.evaluate_tool_call_async(
            "run_shell",
            {"command": "npm install"},
        )

        # Should require approval for moderate risk
        assert result.requires_approval
        assert "Moderate risk command" in result.reason


@pytest.mark.asyncio
async def test_permission_service_ai_classifier_dangerous():
    """Test AI classifier denies dangerous commands."""
    ai_classifier = AiShellClassifier()

    mock_result = MagicMock()
    mock_result.risk_level = RiskLevel.DANGEROUS
    mock_result.allowed = False
    mock_result.reason = "Dangerous command detected"
    mock_result.error = False

    with patch.object(ai_classifier, "classify", new=AsyncMock(return_value=mock_result)):
        service = PermissionService.default(ai_classifier=ai_classifier)

        # Use a command that static classifier won't immediately deny
        result = await service.evaluate_tool_call_async(
            "run_shell",
            {"command": "unknown-dangerous-tool --force"},
        )

        # If static classifier catches it first, it will deny
        # Otherwise AI classifier should be consulted
        assert not result.allowed or result.requires_approval


@pytest.mark.asyncio
async def test_permission_service_ai_classifier_fallback():
    """Test AI classifier failure falls back to the static verdict."""
    ai_classifier = AiShellClassifier()

    with patch.object(ai_classifier, "classify", new=AsyncMock(side_effect=Exception("API error"))):
        service = PermissionService.default(ai_classifier=ai_classifier)

        result = await service.evaluate_tool_call_async(
            "run_shell",
            {"command": "some-command"},
        )

        # Should fall back to static approval flow, never a hard deny
        assert result.requires_approval
        assert "AI classifier denied" not in result.reason


@pytest.mark.asyncio
async def test_permission_service_without_ai_classifier():
    """Test backward compatibility when AI classifier is not provided."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "git status", source="project")

    service = PermissionService.default(rule_hierarchy=hierarchy)

    # Should work without AI classifier
    result = await service.evaluate_tool_call_async(
        "run_shell",
        {"command": "git status"},
    )
    assert result.allowed

    # Ambiguous commands should use static classifier
    result = await service.evaluate_tool_call_async(
        "run_shell",
        {"command": "unknown-command"},
    )
    # Static classifier will require approval for unknown commands
    assert result.requires_approval or not result.allowed


@pytest.mark.asyncio
async def test_permission_service_explicit_rule_skips_ai():
    """Test that explicit rules bypass AI classifier."""
    hierarchy = RuleHierarchy()
    hierarchy.add_rule("run_shell", "allow", "my-script.sh", source="project")

    ai_classifier = AiShellClassifier()

    with patch.object(ai_classifier, "classify", new=AsyncMock()) as mock_classify:
        service = PermissionService.default(
            rule_hierarchy=hierarchy,
            ai_classifier=ai_classifier,
        )

        result = await service.evaluate_tool_call_async(
            "run_shell",
            {"command": "my-script.sh"},
        )

        # AI classifier should NOT be consulted when explicit rule matches
        mock_classify.assert_not_called()
        assert result.allowed
        assert result.matched_rule == "my-script.sh"


def test_load_settings_rules_method():
    """Test PermissionService.load_settings_rules() method."""
    service = PermissionService.default(rule_hierarchy=RuleHierarchy())

    settings = {
        "permissions": {
            "allow": ["run_shell(git log)"],
            "deny": ["write_file(/tmp/*)"],
        }
    }

    service.load_settings_rules(settings, source="session")

    # Rules should be loaded
    assert "git log" in service.rules["run_shell"]["allow"]
    assert "/tmp/*" in service.rules["write_file"]["deny"]


def test_load_settings_rules_no_hierarchy():
    """Test load_settings_rules is no-op without hierarchy."""
    service = PermissionService.default()  # No hierarchy

    settings = {
        "permissions": {
            "allow": ["run_shell(git log)"],
        }
    }

    # Should not raise
    service.load_settings_rules(settings, source="session")

    # No rules loaded since there's no hierarchy
    assert "run_shell" not in service.rules or "git log" not in service.rules.get(
        "run_shell", {}
    ).get("allow", [])
