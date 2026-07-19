from koder_agent.harness.sandbox.backend import SandboxBackendCapabilities
from koder_agent.harness.sandbox.enforcement import active_policy_enforcements
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.sandbox.registry import get_backend_status


def test_network_policy_defaults_to_disabled_for_sandboxed_shell():
    policy = SandboxPolicy.from_config({"enabled": True})

    assert policy.network_access is False
    assert policy.allowed_domains == ()
    assert policy.denied_domains == ()


def test_domain_lists_are_accepted_as_policy_only_for_unix_local():
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "allowedDomains": ["example.com"],
            "deniedDomains": ["metadata.google.internal"],
        }
    )
    status = get_backend_status("unix-local")

    assert policy.allowed_domains == ("example.com",)
    assert policy.denied_domains == ("metadata.google.internal",)
    assert status.capabilities.supports_network_policy == "unsupported"


def test_sandbox_status_labels_domain_lists_as_unenforced(tmp_path, monkeypatch):
    import asyncio
    import json

    from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "backend": "unix-local",
                    "networkAccess": False,
                    "allowedDomains": ["example.com"],
                    "deniedDomains": ["metadata.google.internal"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    output = asyncio.run(handler.handle_slash_input("/sandbox status", scheduler=None))

    assert "network_policy_enforcement: unsupported" in output
    assert "allowed_domains: example.com (policy metadata, not enforced)" in output
    assert "denied_domains: metadata.google.internal (policy metadata, not enforced)" in output


def test_policy_enforcement_never_counts_command_preflight_as_complete_enforcement():
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "e2b",
            "networkAccess": False,
            "denyRead": ["secret.txt"],
        }
    )
    capabilities = SandboxBackendCapabilities(
        supports_workspace_isolation="enforced",
        supports_network_policy="enforced",
        supports_domain_policy="unsupported",
    )

    by_name = {item.restriction: item for item in active_policy_enforcements(policy, capabilities)}

    assert by_name["workspace isolation"].source == "backend"
    assert by_name["network access disabled"].source == "backend"
    assert by_name["denyRead"].source == "unenforced"
    assert by_name["protectedPaths"].source == "unenforced"
    assert by_name["denyWrite"].source == "unenforced"


def test_e2b_domain_lists_remain_unenforced_even_with_boolean_network_control():
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "e2b",
            "networkAccess": True,
            "allowedDomains": ["example.com"],
        }
    )
    capabilities = SandboxBackendCapabilities(
        supports_workspace_isolation="enforced",
        supports_network_policy="enforced",
        supports_domain_policy="unsupported",
    )

    by_name = {item.restriction: item for item in active_policy_enforcements(policy, capabilities)}

    assert by_name["domain network policy"].source == "unenforced"
