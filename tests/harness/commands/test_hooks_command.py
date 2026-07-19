import asyncio
import json

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.hooks.project_approval import (
    is_project_hooks_allowed,
    load_project_hook_settings,
    project_hooks_digest,
)
from koder_agent.harness.hooks.runtime import dispatch_command_hooks


def _run(command: str, *, handler: HarnessInteractiveCommandHandler, scheduler=None) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=scheduler))


def test_hooks_command_lists_configured_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo lint",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    handler = HarnessInteractiveCommandHandler()

    output = _run("/hooks", handler=handler)

    assert "hooks:" in output
    assert "count: 1" in output
    assert "PostToolUse" in output
    assert "matcher=Edit|Write" in output


def test_hooks_review_approve_reapprove_and_revoke_workflow(
    tmp_path, monkeypatch, real_project_hook_trust
):
    from koder_agent.harness.hooks import project_approval

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "project-hook-ran"
    changed_marker = tmp_path / "changed-project-hook-ran"
    local_marker = tmp_path / "local-hook-ran"
    settings_dir = project / ".koder"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"printf project > {marker}",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    local_settings_path = settings_dir / "settings.local.json"
    local_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "edit_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"printf local > {local_marker}",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    handler = HarnessInteractiveCommandHandler(emit_console=False)
    handler.interactive_prompt = object()
    scheduler = object()

    first_digest = project_hooks_digest(load_project_hook_settings(project))
    review = _run("/hooks review", handler=handler, scheduler=scheduler)

    assert "status: not approved: project hooks have not been approved" in review
    assert f"digest: {first_digest}" in review
    assert f"project_settings: {settings_dir / 'settings.json'}" in review
    assert f"local_settings: {local_settings_path}" in review
    assert f'"command": "printf project > {marker}"' in review
    assert f'"command": "printf local > {local_marker}"' in review
    assert f"/hooks approve {first_digest}" in review

    approved = _run(f"/hooks approve {first_digest}", handler=handler, scheduler=scheduler)

    assert "Project hook payload approved" in approved
    assert "status: approved" in approved
    assert f'"command": "printf project > {marker}"' in approved
    assert is_project_hooks_allowed(project) is True

    dispatch_result = dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "test"},
    )
    assert dispatch_result.matched_hooks == 1
    assert marker.read_text(encoding="utf-8") == "project"

    changed_command = f"printf changed > {changed_marker}"
    changed = json.loads((settings_dir / "settings.json").read_text(encoding="utf-8"))
    changed["hooks"]["SessionStart"][0]["hooks"][0]["command"] = changed_command
    (settings_dir / "settings.json").write_text(json.dumps(changed), encoding="utf-8")
    second_digest = project_hooks_digest(load_project_hook_settings(project))
    assert second_digest != first_digest

    stale_approval = _run(f"/hooks approve {first_digest}", handler=handler, scheduler=scheduler)
    assert "supplied digest does not match" in stale_approval
    assert f"current_digest: {second_digest}" in stale_approval
    assert is_project_hooks_allowed(project) is False

    changed_review = _run("/hooks review", handler=handler, scheduler=scheduler)
    assert "executable hook configuration changed" in changed_review
    assert changed_command in changed_review
    reapproved = _run(f"/hooks approve {second_digest}", handler=handler, scheduler=scheduler)
    assert "status: approved" in reapproved
    assert is_project_hooks_allowed(project) is True

    revoked = _run("/hooks revoke", handler=handler)
    assert "Project hook approval revoked" in revoked
    assert is_project_hooks_allowed(project) is False
    approval_checks = []
    original_allowed = project_approval.is_project_hooks_allowed

    def record_allowed(project_root, settings_by_source=None):
        result = original_allowed(project_root, settings_by_source)
        approval_checks.append((project_root, result, settings_by_source))
        return result

    monkeypatch.setattr(project_approval, "is_project_hooks_allowed", record_allowed)
    dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "test"},
    )
    assert approval_checks and approval_checks[0][1] is False
    assert not changed_marker.exists()


def test_hooks_approve_fails_closed_outside_live_interactive_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "echo denied"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    digest = project_hooks_digest(load_project_hook_settings(project))

    headless_handler = HarnessInteractiveCommandHandler(emit_console=False)
    headless = _run(f"/hooks approve {digest}", handler=headless_handler, scheduler=object())
    assert "only available in a live interactive Koder session" in headless
    assert is_project_hooks_allowed(project) is False

    no_scheduler_handler = HarnessInteractiveCommandHandler(emit_console=False)
    no_scheduler_handler.interactive_prompt = object()
    no_scheduler = _run(f"/hooks approve {digest}", handler=no_scheduler_handler, scheduler=None)
    assert "only available in a live interactive Koder session" in no_scheduler
    assert is_project_hooks_allowed(project) is False


def test_hooks_trust_actions_resolve_owning_project_from_nested_directory(
    tmp_path, monkeypatch, real_project_hook_trust
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "package"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()
    settings_dir = project / ".koder"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "echo nested"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(nested)
    handler = HarnessInteractiveCommandHandler(emit_console=False)
    handler.interactive_prompt = object()
    scheduler = object()
    digest = project_hooks_digest(load_project_hook_settings(project))

    review = _run("/hooks review", handler=handler, scheduler=scheduler)
    assert f"project: {project.resolve()}" in review
    assert f"digest: {digest}" in review

    approved = _run(f"/hooks approve {digest}", handler=handler, scheduler=scheduler)
    assert "status: approved" in approved
    assert is_project_hooks_allowed(project) is True
    dispatched = dispatch_command_hooks(
        cwd=nested,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "nested-test"},
    )
    assert dispatched.matched_hooks == 1

    revoked = _run("/hooks revoke", handler=handler, scheduler=scheduler)
    assert f"project: {project.resolve()}" in revoked
    assert is_project_hooks_allowed(project) is False
