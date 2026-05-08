from pathlib import Path

from koder_agent.harness import github_actions


def test_normalize_repo_slug_accepts_common_github_forms():
    assert github_actions.normalize_repo_slug("owner/repo") == "owner/repo"
    assert github_actions.normalize_repo_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert github_actions.normalize_repo_slug("git@github.com:owner/repo.git") == "owner/repo"
    assert github_actions.normalize_repo_slug("not a repo") is None


def test_status_reports_missing_gh_without_mutation(monkeypatch, tmp_path):
    monkeypatch.setattr(github_actions.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        github_actions,
        "_run",
        lambda *_args, **_kwargs: github_actions.CommandResult(returncode=1, stderr="no remote"),
    )

    output = github_actions.render_github_actions_status(cwd=tmp_path)

    assert "github_actions:" in output
    assert "integration_scope: local gh CLI + GitHub Actions workflow" in output
    assert "repo: not detected" in output
    assert "gh: not found" in output
    assert "mutation" not in output


def test_plan_detects_current_repo_and_secret_state(monkeypatch, tmp_path):
    monkeypatch.setenv("KODER_API_KEY", "test-secret")
    monkeypatch.setattr(github_actions.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, **_kwargs):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            return github_actions.CommandResult(0, stdout="https://github.com/acme/demo.git\n")
        if args == ["gh", "--version"]:
            return github_actions.CommandResult(0, stdout="gh version 2.0.0\n")
        if args == ["gh", "auth", "status", "-h", "github.com"]:
            return github_actions.CommandResult(0, stdout="Token scopes: repo, workflow\n")
        return github_actions.CommandResult(1, stderr="unexpected")

    monkeypatch.setattr(github_actions, "_run", fake_run)

    output = github_actions.render_github_actions_command(["plan"], cwd=tmp_path)

    assert "github_actions: plan" in output
    assert "repo: acme/demo" in output
    assert "- assistant: .github/workflows/koder.yml" in output
    assert "- review: .github/workflows/koder-review.yml" in output
    assert "secret_env_status: configured" in output
    assert "mutation: none" in output


def test_plan_apply_command_preserves_selected_options(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_KODER_KEY", "test-secret")
    monkeypatch.setattr(github_actions.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, **_kwargs):
        if args == ["gh", "--version"]:
            return github_actions.CommandResult(0, stdout="gh version 2.0.0\n")
        if args == ["gh", "auth", "status", "-h", "github.com"]:
            return github_actions.CommandResult(0, stdout="Token scopes: repo, workflow\n")
        return github_actions.CommandResult(1, stderr="unexpected")

    monkeypatch.setattr(github_actions, "_run", fake_run)

    output = github_actions.render_github_actions_command(
        [
            "plan",
            "acme/demo",
            "--workflow",
            "assistant",
            "--secret-env",
            "CUSTOM_KODER_KEY",
            "--model",
            "gpt-4.1",
            "--base-url",
            "https://scenario-base.invalid/v1",
            "--branch",
            "setup/koder",
        ],
        cwd=tmp_path,
    )

    assert (
        "apply_command: /install-github-app apply acme/demo --workflow assistant "
        "--secret-env CUSTOM_KODER_KEY --model gpt-4.1 "
        "--base-url https://scenario-base.invalid/v1 --branch setup/koder"
    ) in output


def test_apply_blocks_before_remote_mutation_when_secret_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("KODER_API_KEY", raising=False)
    monkeypatch.setattr(github_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return github_actions.CommandResult(0)

    monkeypatch.setattr(github_actions, "_run", fake_run)

    output = github_actions.render_github_actions_command(
        ["apply", "acme/demo", "--branch", "setup/koder"], cwd=tmp_path
    )

    assert "github_actions: blocked" in output
    assert "step: secret" in output
    assert calls == []


def test_apply_writes_workflows_and_streams_secret_via_stdin(monkeypatch, tmp_path):
    monkeypatch.setenv("KODER_API_KEY", "secret-value")
    monkeypatch.setattr(github_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def fake_run(args, *, input_text=None, **_kwargs):
        calls.append((tuple(args), input_text))
        if args == ["gh", "auth", "status", "-h", "github.com"]:
            return github_actions.CommandResult(0, stdout="Token scopes: repo, workflow\n")
        if args == ["gh", "api", "repos/acme/demo", "--jq", ".permissions.admin"]:
            return github_actions.CommandResult(0, stdout="true\n")
        if args == ["gh", "api", "repos/acme/demo", "--jq", ".default_branch"]:
            return github_actions.CommandResult(0, stdout="main\n")
        if args == ["gh", "api", "repos/acme/demo/git/ref/heads/main", "--jq", ".object.sha"]:
            return github_actions.CommandResult(0, stdout="abc123\n")
        if "repos/acme/demo/git/refs" in args:
            return github_actions.CommandResult(0, stdout="{}")
        if any("/contents/" in arg and "?ref=setup/koder" in arg for arg in args):
            return github_actions.CommandResult(1, stderr="Not Found")
        if any("/contents/" in arg for arg in args):
            return github_actions.CommandResult(0, stdout="{}")
        if args == ["gh", "secret", "set", "KODER_API_KEY", "--repo", "acme/demo"]:
            return github_actions.CommandResult(0, stdout="")
        return github_actions.CommandResult(1, stderr=f"unexpected args: {args}")

    monkeypatch.setattr(github_actions, "_run", fake_run)

    output = github_actions.render_github_actions_command(
        ["apply", "acme/demo", "--branch", "setup/koder"], cwd=Path(tmp_path)
    )

    assert "github_actions: applied" in output
    assert "branch: setup/koder" in output
    assert "- .github/workflows/koder.yml: written" in output
    assert "- .github/workflows/koder-review.yml: written" in output
    assert "secret: KODER_API_KEY" in output
    assert "compare_url: https://github.com/acme/demo/compare/main...setup/koder" in output
    assert any(call[1] == "secret-value" for call in calls)
    assert all("secret-value" not in " ".join(call[0]) for call in calls)
