import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.rules import (
    PermissionRule,
    match_permission_rule,
    parse_permission_rule,
)
from koder_agent.harness.permissions.shell_classifier import classify_shell_command


def test_shell_classifier_allows_dev_null_redirection():
    result = classify_shell_command('find . -name "SKILL.md" 2>/dev/null | head -1')
    assert result.allowed is True


def test_shell_classifier_blocks_other_device_redirects():
    result = classify_shell_command("echo test >/dev/sda")
    assert result.allowed is False


def test_parse_permission_rule_supports_legacy_prefix_syntax():
    rule = parse_permission_rule("git:*")
    assert rule.kind == "prefix"
    assert rule.value == "git"


def test_match_permission_rule_supports_wildcards():
    rule = PermissionRule(kind="wildcard", value="npm *")
    assert match_permission_rule(rule, "npm test") is True
    assert match_permission_rule(rule, "python -m pytest") is False
