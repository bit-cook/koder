from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.sandbox.workspace import protected_write_violation, read_only_violation


def test_policy_maps_legacy_disabled_to_danger_full_access():
    policy = SandboxPolicy.from_config({"enabled": False})

    assert policy.mode == "danger-full-access"
    assert policy.enabled is False


def test_policy_maps_enabled_to_workspace_write():
    policy = SandboxPolicy.from_config({"enabled": True})

    assert policy.mode == "workspace-write"
    assert policy.enabled is True


def test_read_only_policy_blocks_mutating_shell_commands():
    policy = SandboxPolicy(mode="read-only")

    assert read_only_violation("touch file.txt", policy=policy)
    assert read_only_violation("rg TODO .", policy=policy) is None


def test_protected_path_preflight_blocks_exact_write_targets(tmp_path):
    policy = SandboxPolicy(mode="workspace-write", protected_paths=(".git", ".koder"))

    assert protected_write_violation("touch .git/config", policy=policy, repo_root=tmp_path)
    assert protected_write_violation("touch src/app.py", policy=policy, repo_root=tmp_path) is None
