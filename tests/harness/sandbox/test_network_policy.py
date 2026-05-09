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
