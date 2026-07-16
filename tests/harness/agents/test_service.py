import asyncio
import shutil
import subprocess
import sys
import tempfile
import types
from dataclasses import replace
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.agents.definitions import AgentDefinition  # noqa: E402
from koder_agent.harness.agents.service import (  # noqa: E402
    AgentService,
    agent_definition_matches_record,
    resolve_agent_record_origin,
)
from koder_agent.tools.plan_mode import _get_plan_service, _set_plan_service  # noqa: E402
from koder_agent.tools.todo import get_todo_store  # noqa: E402


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _init_git_repo(repo_root: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_agent_service_can_spawn_and_message_agent():
    service = AgentService.for_test()
    agent_id = service.spawn("default")
    service.send(agent_id, "hello")
    assert service.read_mailbox(agent_id)[0].content == "hello"


def test_agent_service_for_test_without_root_uses_cleanable_temp_dir():
    service = AgentService.for_test()
    output_root = service.output_root.resolve()

    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        assert _is_relative_to(output_root, temp_root)
        assert not _is_relative_to(output_root, Path.cwd().resolve())

        agent_id = service.spawn("default")
        assert (output_root / f"{agent_id}.json").exists()
    finally:
        service.close()

    assert not output_root.exists()


def test_agent_service_handles_delayed_worker_response_without_corrupting_state():
    service = AgentService.for_test()
    agent_id = service.spawn("default")
    result = service.mark_worker_delayed(agent_id)
    assert result.state_preserved is True


def test_agent_service_launches_background_agent_and_writes_output(tmp_path, monkeypatch):
    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        assert agent_definition.agent_type == "general-purpose"
        assert prompt == "Investigate the auth flow"
        assert session_id.startswith("subagent-")
        assert seed_items == [{"role": "user", "content": "hello"}]
        assert cwd == str(tmp_path)
        return "subagent result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Investigate the auth flow",
            description="Auth investigation",
            seed_items=[{"role": "user", "content": "hello"}],
            cwd=tmp_path,
        )
        await service.wait(record.id)
        updated = service.get(record.id)
        assert updated.state == "completed"
        assert updated.summary == "Completed: subagent result"
        assert updated.summary_updated_at is not None
        assert updated.output_path is not None
        assert Path(updated.output_path).read_text(encoding="utf-8") == "subagent result"

    asyncio.run(run_case())


def test_agent_service_records_redacted_model_config_snapshot(tmp_path, monkeypatch):
    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "subagent result"

    def fake_snapshot(model_override):
        assert model_override is None
        return {
            "model_name": "litellm/claude_oauth/claude-sonnet-4-6",
            "api_key": "secret-oauth-token",
            "base_url": "https://proxy.example/v1",
            "native_openai": False,
            "reasoning_effort": "high",
            "litellm_kwargs": {
                "model": "claude_oauth/claude-sonnet-4-6",
                "api_key": "secret-oauth-token",
                "base_url": "https://proxy.example/v1",
                "extra_headers": {"x-oauth-provider": "claude"},
            },
        }

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.service.get_model_client_snapshot", fake_snapshot
    )

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Investigate the auth flow",
            description="Auth investigation",
            cwd=tmp_path,
        )
        await service.wait(record.id)
        updated = service.get(record.id)
        assert updated.model_config == {
            "model_override": "inherit",
            "model_name": "litellm/claude_oauth/claude-sonnet-4-6",
            "provider": "claude_oauth",
            "base_url": "https://proxy.example/v1",
            "native_openai": False,
            "api_key_present": True,
            "reasoning_effort": "high",
            "litellm_model": "claude_oauth/claude-sonnet-4-6",
            "oauth_provider": "claude",
            "oauth_headers_present": True,
        }
        saved = (service.output_root / f"{record.id}.json").read_text(encoding="utf-8")
        assert "secret-oauth-token" not in saved
        reloaded = AgentService.for_test(tmp_path)
        assert reloaded.get(record.id).model_config == updated.model_config

    asyncio.run(run_case())


def test_agent_service_refreshes_summary_from_persisted_output(tmp_path, monkeypatch):
    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "Reviewed runtime output\nwith details"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Summarize runtime output",
            description="Runtime summary task",
        )
        assert record.summary == "Working: Runtime summary task"
        await service.wait(record.id)
        reloaded = AgentService.for_test(tmp_path)
        refreshed = reloaded.refresh_summary(record.id)
        assert refreshed.summary == "Completed: Reviewed runtime output"
        assert refreshed.summary_updated_at is not None

    asyncio.run(run_case())


def test_agent_service_records_failure_summary(tmp_path, monkeypatch):
    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Fail cleanly",
            description="Failure summary task",
        )
        await service.wait(record.id)
        updated = service.get(record.id)
        assert updated.state == "failed"
        assert updated.error == "provider unavailable"
        assert updated.summary == "Failed: provider unavailable"

    asyncio.run(run_case())


def test_agent_service_can_resume_background_agent_with_same_session(tmp_path, monkeypatch):
    seen_session_ids: list[str] = []
    seen_stores = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_session_ids.append(session_id)
        store = get_todo_store()
        seen_stores.append(store)
        if prompt == "first task":
            store.todos = [{"content": "persisted", "status": "pending", "id": "todo-1"}]
        else:
            assert store.todos[0]["content"] == "persisted"
        return f"result for {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="first task",
            description="Initial task",
        )
        await service.wait(record.id)
        resumed = await service.resume_background(
            agent_id=record.id,
            agent_definition=definition,
            prompt="resume task",
        )
        await service.wait(resumed.id)
        assert len(seen_session_ids) == 2
        assert seen_session_ids[0] == seen_session_ids[1]
        assert seen_stores[0] is seen_stores[1]
        assert seen_stores[0].identity.agent_id == record.id

    asyncio.run(run_case())


def test_agent_service_isolates_concurrent_agents_with_same_definition(tmp_path, monkeypatch):
    stores = {}

    async def fake_execute(*, prompt, **_kwargs):
        store = get_todo_store()
        store.todos = [{"content": prompt, "status": "pending", "id": prompt}]
        stores[prompt] = store
        await asyncio.sleep(0)
        assert store.todos[0]["content"] == prompt
        return prompt

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        first, second = await asyncio.gather(
            service.launch_background(
                agent_definition=definition,
                prompt="first",
                description="First",
            ),
            service.launch_background(
                agent_definition=definition,
                prompt="second",
                description="Second",
            ),
        )
        await asyncio.gather(service.wait(first.id), service.wait(second.id))
        assert stores["first"] is not stores["second"]
        assert stores["first"].identity.agent_id == first.id
        assert stores["second"].identity.agent_id == second.id

    asyncio.run(run_case())


def test_agent_service_releases_todo_store_on_explicit_cleanup(tmp_path, monkeypatch):
    seen_stores = []

    async def fake_execute(**_kwargs):
        seen_stores.append(get_todo_store())
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="first",
            description="First",
        )
        await service.wait(record.id)
        original = seen_stores[0]
        service.release_agent(record.id)
        replacement = service._get_or_create_todo_store(record.id, record.session_id)
        assert replacement is not original
        assert replacement.todos == []

    asyncio.run(run_case())


def test_agent_service_launches_isolated_worktree_agent_in_worktree_cwd(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    seen_cwds: list[str] = []
    seen_git_dirs: list[bool] = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_cwds.append(cwd)
        seen_git_dirs.append((Path(cwd) / ".git").exists())
        return "isolated result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
        isolation="worktree",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Investigate in worktree",
            description="Worktree task",
            cwd=repo_root,
        )
        await service.wait(record.id)
        assert seen_cwds
        assert seen_cwds[0] != str(repo_root)
        # The agent ran inside a real git worktree...
        assert seen_git_dirs == [True]
        # ...and because the run left no changes, the clean worktree was
        # removed after completion and the record no longer points at it.
        updated = service.get(record.id)
        assert updated.worktree_path is None
        assert not Path(seen_cwds[0]).exists()

    asyncio.run(run_case())


def test_agent_service_keeps_dirty_worktree_after_completion(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    seen_cwds: list[str] = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_cwds.append(cwd)
        (Path(cwd) / "result.txt").write_text("agent output\n", encoding="utf-8")
        return "isolated result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
        isolation="worktree",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Produce a file in the worktree",
            description="Worktree task",
            cwd=repo_root,
        )
        await service.wait(record.id)
        updated = service.get(record.id)
        assert updated.worktree_path == seen_cwds[0]
        assert Path(seen_cwds[0]).exists()
        assert (Path(seen_cwds[0]) / "result.txt").exists()

    asyncio.run(run_case())


def _list_sync_agent_branches(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "branch", "--list", "sync-agent/*", "--format=%(refname:short)"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_agent_service_run_sync_removes_clean_worktree_and_branch(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    seen_cwds: list[str] = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_cwds.append(cwd)
        return "sync isolated result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
        isolation="worktree",
    )

    async def run_case():
        result = await service.run_sync(
            agent_definition=definition,
            prompt="Investigate in worktree",
            cwd=repo_root,
        )
        assert result == "sync isolated result"
        assert seen_cwds and seen_cwds[0] != str(repo_root)
        # The clean worktree and its sync-agent/* branch are removed after the run.
        assert not Path(seen_cwds[0]).exists()
        assert _list_sync_agent_branches(repo_root) == []

    asyncio.run(run_case())


def test_agent_service_run_sync_keeps_dirty_worktree(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    seen_cwds: list[str] = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_cwds.append(cwd)
        (Path(cwd) / "result.txt").write_text("agent output\n", encoding="utf-8")
        return "sync isolated result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
        isolation="worktree",
    )

    async def run_case():
        await service.run_sync(
            agent_definition=definition,
            prompt="Produce a file in the worktree",
            cwd=repo_root,
        )
        # Dirty worktrees are kept so the user can inspect or merge the work.
        assert Path(seen_cwds[0]).exists()
        assert (Path(seen_cwds[0]) / "result.txt").exists()
        assert len(_list_sync_agent_branches(repo_root)) == 1

    asyncio.run(run_case())


def test_agent_service_run_sync_removes_clean_worktree_on_failure(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    seen_cwds: list[str] = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_cwds.append(cwd)
        raise RuntimeError("agent exploded")

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
        isolation="worktree",
    )

    async def run_case():
        with pytest.raises(RuntimeError, match="agent exploded"):
            await service.run_sync(
                agent_definition=definition,
                prompt="Fail inside worktree",
                cwd=repo_root,
            )
        # A failed run that left no changes has nothing to inspect; the
        # worktree and branch must not leak.
        assert not Path(seen_cwds[0]).exists()
        assert _list_sync_agent_branches(repo_root) == []

    asyncio.run(run_case())


def test_agent_service_can_reload_agent_record_from_disk(tmp_path, monkeypatch):
    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "persisted result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Persist this run",
            description="Persisted task",
        )
        await service.wait(record.id)
        reloaded = AgentService.for_test(tmp_path)
        reloaded_record = reloaded.get(record.id)
        assert reloaded_record.output_path == record.output_path
        assert reloaded_record.origin_cwd == str(Path.cwd().resolve())
        assert agent_definition_matches_record(reloaded_record, definition)
        assert Path(record.output_path).read_text(encoding="utf-8") == "persisted result"

    asyncio.run(run_case())


def test_agent_service_rejects_tampered_origin_and_definition_on_resume(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()

    async def fake_execute(**_kwargs):
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="worker",
        when_to_use="Does work",
        system_prompt="Original worker.",
        source="projectSettings",
        filename="worker",
        base_dir=str(project / ".koder" / "agents"),
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="first",
            description="First",
            cwd=project,
        )
        await service.wait(record.id)

        changed_definition = AgentDefinition(
            agent_type="worker",
            when_to_use="Does work",
            system_prompt="Malicious replacement.",
            source="projectSettings",
            filename="worker",
            base_dir=str(project / ".koder" / "agents"),
        )
        with pytest.raises(ValueError, match="definition provenance"):
            await service.resume_background(
                agent_id=record.id,
                agent_definition=changed_definition,
                prompt="continue",
                cwd=project,
            )

        service._agents[record.id] = replace(record, origin_cwd="../../tmp")
        with pytest.raises(ValueError, match="must be absolute"):
            resolve_agent_record_origin(service.get(record.id))

    asyncio.run(run_case())


def test_agent_service_can_cancel_background_agent(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        started.set()
        await asyncio.sleep(60)
        return "never reached"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Long running task",
            description="Long running task",
        )
        await started.wait()
        cancelled = await service.cancel_background(record.id)
        assert cancelled.state == "cancelled"
        assert Path(cancelled.output_path).read_text(encoding="utf-8") == "Cancelled"

    asyncio.run(run_case())


def test_agent_service_applies_plan_mode_per_background_run(tmp_path, monkeypatch):
    observed_modes: dict[str, tuple[str, bool]] = {}

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        await asyncio.sleep(0)
        service = _get_plan_service()
        observed_modes[prompt] = (service.mode, service.is_plan_mode())
        return f"result for {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def run_case():
        plan_record = await service.launch_background(
            agent_definition=definition,
            prompt="plan-task",
            description="Plan task",
            permission_mode="plan",
        )
        default_record = await service.launch_background(
            agent_definition=definition,
            prompt="default-task",
            description="Default task",
            permission_mode="default",
        )
        await service.wait(plan_record.id)
        await service.wait(default_record.id)

    try:
        asyncio.run(run_case())
    finally:
        _set_plan_service(None)

    assert observed_modes["plan-task"] == ("plan", True)
    assert observed_modes["default-task"] == ("default", False)
