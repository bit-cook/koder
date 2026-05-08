from koder_agent.harness.permissions.powershell_classifier import classify_powershell_command
from koder_agent.harness.permissions.service import PermissionService


def test_powershell_classifier_allows_read_only_cmdlets():
    decision = classify_powershell_command("Get-ChildItem | Select-String fixture")

    assert decision.allowed is True
    assert decision.read_only is True
    assert decision.requires_approval is False
    assert decision.reason == "read-only PowerShell command"


def test_powershell_classifier_requires_approval_for_mutations():
    decision = classify_powershell_command("New-Item fixture.txt")

    assert decision.allowed is True
    assert decision.read_only is False
    assert decision.requires_approval is True


def test_powershell_classifier_blocks_dangerous_cmdlets():
    decision = classify_powershell_command("Invoke-Expression $payload")

    assert decision.allowed is False
    assert decision.destructive is True
    assert decision.reason == "dangerous PowerShell command detected"


def test_permission_service_targets_run_powershell_rules():
    service = PermissionService.default()
    service.add_rule("run_powershell", "allow", "New-Item fixture.txt")

    decision = service.evaluate_tool_call("run_powershell", {"command": "New-Item fixture.txt"})

    assert decision.allowed is True
    assert decision.tool_name == "run_powershell"
    assert decision.matched_rule == "New-Item fixture.txt"
