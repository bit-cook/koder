"""Production wiring tests for hook events that previously never fired.

Each test drives the REAL production dispatch path (not a bare
dispatch_command_hooks call) and asserts the configured hook command ran.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


def _write_hooks(project: Path, hooks: dict) -> None:
    (project / ".koder").mkdir(parents=True, exist_ok=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps({"hooks": hooks}), encoding="utf-8"
    )


def _marker_hook(marker: Path, matcher: str | None = None) -> list[dict]:
    rule: dict = {
        "hooks": [
            {
                "type": "command",
                "command": (
                    'python -c "import sys, pathlib; '
                    f"pathlib.Path(r'{marker}').write_text(sys.stdin.read())\""
                ),
            }
        ]
    }
    if matcher is not None:
        rule["matcher"] = matcher
    return [rule]


# ---------------------------------------------------------------------------
# PermissionDenied / PermissionRequest / Notification via enforce_tool_permission
# ---------------------------------------------------------------------------


def test_permission_denied_hook_fires_from_tool_chain(tmp_path, monkeypatch):
    from koder_agent.harness.permissions.service import PermissionService
    from koder_agent.tools.permission_context import (
        enforce_tool_permission,
        reset_tool_permission_context,
        set_tool_permission_context,
    )

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "denied.json"
    _write_hooks(project, {"PermissionDenied": _marker_hook(marker)})
    monkeypatch.chdir(project)

    service = PermissionService.default()
    service.add_rule("run_shell", "deny", "rm *")
    token = set_tool_permission_context(service)
    try:
        result = asyncio.run(
            enforce_tool_permission("run_shell", json.dumps({"command": "rm -rf /tmp/x"}))
        )
    finally:
        reset_tool_permission_context(token)

    assert result is not None and "Permission denied" in result
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "PermissionDenied"
    assert payload["tool_name"] == "run_shell"
    assert payload["tool_input"]["command"] == "rm -rf /tmp/x"


def test_permission_request_hook_can_allow_and_deny(tmp_path, monkeypatch):
    from koder_agent.harness.permissions.service import PermissionService
    from koder_agent.tools.permission_context import (
        enforce_tool_permission,
        reset_tool_permission_context,
        set_tool_permission_context,
    )

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    notification_marker = tmp_path / "notify.json"
    decision_file = tmp_path / "decision.json"
    # PermissionRequest hook reads its decision from a file so one test can
    # exercise both behaviors; Notification hook writes a marker.
    _write_hooks(
        project,
        {
            "PermissionRequest": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python -c \"print(open(r'{decision_file}').read())\"",
                        }
                    ]
                }
            ],
            "Notification": _marker_hook(notification_marker, matcher="permission_prompt"),
        },
    )
    monkeypatch.chdir(project)

    service = PermissionService.default()
    service.add_rule("run_shell", "ask", "git push*")
    token = set_tool_permission_context(service)
    try:
        decision_file.write_text(
            json.dumps({"hookSpecificOutput": {"decision": {"behavior": "allow"}}}),
            encoding="utf-8",
        )
        allowed = asyncio.run(
            enforce_tool_permission("run_shell", json.dumps({"command": "git push origin main"}))
        )
        assert allowed is None

        decision_file.write_text(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "decision": {"behavior": "deny", "message": "denied by policy hook"}
                    }
                }
            ),
            encoding="utf-8",
        )
        denied = asyncio.run(
            enforce_tool_permission("run_shell", json.dumps({"command": "git push origin main"}))
        )
        assert denied is not None and "denied by policy hook" in denied
    finally:
        reset_tool_permission_context(token)

    notify_payload = json.loads(notification_marker.read_text(encoding="utf-8"))
    assert notify_payload["event"] == "Notification"
    assert notify_payload["notification_type"] == "permission_prompt"


# ---------------------------------------------------------------------------
# PostToolUseFailure via the SDK failure_error_function
# ---------------------------------------------------------------------------


def test_post_tool_use_failure_hook_fires_when_tool_raises(tmp_path, monkeypatch):
    from koder_agent.tools.compat import function_tool

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "failure.json"
    _write_hooks(project, {"PostToolUseFailure": _marker_hook(marker)})
    monkeypatch.chdir(project)

    @function_tool
    def exploding_tool(x: int) -> str:
        """Always fails."""
        raise ValueError("kaboom")

    result = asyncio.run(exploding_tool.on_invoke_tool(None, json.dumps({"x": 1})))

    assert "kaboom" in str(result)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "PostToolUseFailure"
    assert payload["tool_name"] == "exploding_tool"
    assert payload["tool_input"] == {"x": 1}
    assert "kaboom" in payload["error"]


# ---------------------------------------------------------------------------
# PreCompact / PostCompact on automatic compaction
# ---------------------------------------------------------------------------


def test_auto_compact_dispatches_pre_and_post_compact(tmp_path, monkeypatch):
    from koder_agent.core.scheduler import AgentScheduler
    from koder_agent.harness.memory.compact import CompactionResult

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    pre = tmp_path / "precompact.json"
    post = tmp_path / "postcompact.json"
    _write_hooks(
        project,
        {
            "PreCompact": _marker_hook(pre, matcher="auto"),
            "PostCompact": _marker_hook(post, matcher="auto"),
        },
    )
    monkeypatch.chdir(project)

    class _Session:
        session_id = "auto-compact-session"

        async def get_items(self):
            return [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
            ]

        async def replace_items(self, items):
            pass

        summarization_threshold = 100

    scheduler = AgentScheduler.__new__(AgentScheduler)
    scheduler.session = _Session()
    scheduler.todo_store = SimpleNamespace(todos=[])
    scheduler._auto_compact = SimpleNamespace(
        record_success=lambda: None, record_failure=lambda: None
    )
    scheduler._compact_keep_recent = lambda: 2

    async def fake_refresh(items):
        return 1000

    scheduler.refresh_context_usage_from_session = fake_refresh

    async def fake_compact(messages, keep_recent=0):
        return CompactionResult(
            summary="compact summary",
            kept_messages=[{"role": "user", "content": "one"}],
            token_count=0,
            original_count=len(messages),
        )

    monkeypatch.setattr("koder_agent.core.scheduler.llm_compact_messages", fake_compact)

    asyncio.run(scheduler._run_auto_compact())

    pre_payload = json.loads(pre.read_text(encoding="utf-8"))
    assert pre_payload["event"] == "PreCompact"
    assert pre_payload["trigger"] == "auto"
    post_payload = json.loads(post.read_text(encoding="utf-8"))
    assert post_payload["event"] == "PostCompact"
    assert post_payload["trigger"] == "auto"
    assert post_payload["summary"] == "compact summary"


# ---------------------------------------------------------------------------
# SubagentStart / SubagentStop via the full hook runner (user-scope settings)
# ---------------------------------------------------------------------------


def test_subagent_hooks_honor_user_scope_settings(tmp_path, monkeypatch):
    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.hooks import SubagentLifecycleHooks

    home = tmp_path / "home"
    (home / ".koder").mkdir(parents=True)
    start_marker = tmp_path / "sub-start.json"
    stop_marker = tmp_path / "sub-stop.json"
    # User-scope settings: the old mini-runner only read project scope, so
    # this test proves the upgrade to the full hook runner.
    (home / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SubagentStart": _marker_hook(start_marker),
                    "SubagentStop": _marker_hook(stop_marker),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    definition = AgentDefinition(
        agent_type="test-agent",
        when_to_use="test",
        system_prompt="x",
        source="flagSettings",
    )
    hooks = SubagentLifecycleHooks(agent_definition=definition, cwd=project)

    asyncio.run(hooks.on_agent_start(None, SimpleNamespace(name="test-agent")))
    asyncio.run(hooks.on_agent_end(None, SimpleNamespace(name="test-agent"), "done"))

    assert json.loads(start_marker.read_text(encoding="utf-8"))["event"] == "SubagentStart"
    stop_payload = json.loads(stop_marker.read_text(encoding="utf-8"))
    assert stop_payload["event"] == "SubagentStop"
    assert stop_payload["output"] == "done"


# ---------------------------------------------------------------------------
# WorktreeRemove via clean-worktree cleanup
# ---------------------------------------------------------------------------


def test_worktree_remove_if_clean_fires_hook_and_removes(tmp_path, monkeypatch):
    import subprocess

    from koder_agent.harness.worktree.service import WorktreeService

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    marker = tmp_path / "worktree-remove.json"
    repo.mkdir()
    _write_hooks(repo, {"WorktreeRemove": _marker_hook(marker)})
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)

    service = WorktreeService.for_test(repo)
    created = service.create("agent/clean-demo")
    assert created.path.exists()

    removed = service.remove_if_clean(created.path, branch=created.branch)

    assert removed is True
    assert not created.path.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "WorktreeRemove"


def test_worktree_remove_if_clean_keeps_dirty_worktree(tmp_path, monkeypatch):
    import subprocess

    from koder_agent.harness.worktree.service import WorktreeService

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    marker = tmp_path / "worktree-remove.json"
    repo.mkdir()
    _write_hooks(repo, {"WorktreeRemove": _marker_hook(marker)})
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)

    service = WorktreeService.for_test(repo)
    created = service.create("agent/dirty-demo")
    (created.path / "work.txt").write_text("uncommitted\n", encoding="utf-8")

    removed = service.remove_if_clean(created.path, branch=created.branch)

    assert removed is False
    assert created.path.exists()
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Setup on the onboarding panel path
# ---------------------------------------------------------------------------


def test_setup_hook_fires_when_onboarding_incomplete(tmp_path, monkeypatch):
    from koder_agent.harness.hooks.runtime import dispatch_command_hooks
    from koder_agent.harness.onboarding import check_onboarding_state, get_onboarding_steps

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "setup.json"
    _write_hooks(project, {"Setup": _marker_hook(marker)})
    monkeypatch.chdir(project)

    # Mirror the session_flow onboarding branch with a forced-incomplete state.
    state = check_onboarding_state(Path.cwd(), env={})
    missing_steps = get_onboarding_steps(state) or ["configure a provider API key"]
    dispatch_command_hooks(
        cwd=Path.cwd(),
        event_name="Setup",
        match_value=None,
        payload={"event": "Setup", "missing_steps": missing_steps},
    )

    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "Setup"


# ---------------------------------------------------------------------------
# ElicitationResult after an elicitation resolves
# ---------------------------------------------------------------------------


def test_elicitation_result_hook_fires_after_hook_auto_response(tmp_path, monkeypatch):
    from mcp.types import ElicitRequestFormParams

    from koder_agent.mcp.elicitation import ElicitationHandler

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    result_marker = tmp_path / "elicit-result.json"
    _write_hooks(
        project,
        {
            "Elicitation": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python -c "import json; print(json.dumps({'
                                "'hookSpecificOutput': {'action': 'accept', "
                                "'content': {'name': 'auto'}}}))\""
                            ),
                        }
                    ]
                }
            ],
            "ElicitationResult": _marker_hook(result_marker),
        },
    )
    monkeypatch.chdir(project)

    handler = ElicitationHandler()
    params = ElicitRequestFormParams(
        message="Need a name",
        requestedSchema={"type": "object", "properties": {"name": {"type": "string"}}},
    )
    result = asyncio.run(handler(None, params))

    assert result.action == "accept"
    payload = json.loads(result_marker.read_text(encoding="utf-8"))
    assert payload["event"] == "ElicitationResult"
    assert payload["action"] == "accept"
    assert payload["source"] == "hook"
    assert payload["field_names"] == ["name"]
