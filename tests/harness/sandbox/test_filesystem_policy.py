from koder_agent.harness.sandbox.policy import DEFAULT_PROTECTED_PATHS, SandboxPolicy


def test_default_protected_paths_include_koder_metadata():
    policy = SandboxPolicy.from_config({"enabled": True})

    assert policy.protected_paths == DEFAULT_PROTECTED_PATHS
    assert ".git" in policy.protected_paths
    assert ".koder" in policy.protected_paths
    assert ".agents" in policy.protected_paths
    assert ".codex" in policy.protected_paths


def test_exact_protected_path_roots_are_workspace_relative(tmp_path):
    policy = SandboxPolicy.from_config({"enabled": True, "protectedPaths": [".git", ".koder"]})

    roots = policy.protected_path_roots(tmp_path)

    assert tmp_path / ".git" in roots
    assert tmp_path / ".koder" in roots
