"""Tests for InProcessTeammateRunner."""

import asyncio
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def test_in_process_runner_spawns_and_completes(tmp_path, monkeypatch):
    """InProcessTeammateRunner spawns a teammate that completes."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return f"teammate done: {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="researcher",
            agent_definition=definition,
            prompt="Research the API layer",
            cwd=tmp_path,
        )
        assert result.agent_id is not None
        await runner.wait(result.agent_id)
        record = agent_svc.get(result.agent_id)
        assert record.state == "completed"

    asyncio.run(run_case())


def test_in_process_runner_registers_member(tmp_path, monkeypatch):
    """Spawned teammate is registered as a team member."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="Explore",
        when_to_use="Explore",
        system_prompt="Explorer agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="explorer",
            agent_definition=definition,
            prompt="Find files",
            cwd=tmp_path,
        )
        await runner.wait(result.agent_id)
        members = team_svc.member_records(team_id)
        names = [m.name for m in members]
        assert "explorer" in names

    asyncio.run(run_case())


def test_in_process_runner_marks_idle_on_completion(tmp_path, monkeypatch):
    """Completed teammate stays registered and transitions to an idle worker state."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    # Stub hook dispatch to avoid filesystem issues
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="worker",
            agent_definition=definition,
            prompt="Do work",
            cwd=tmp_path,
        )
        await runner.wait(result.agent_id)
        # Allow the done callback to execute
        await asyncio.sleep(0)
        members = team_svc.member_records(team_id)
        worker = next(m for m in members if m.name == "worker")
        assert worker.is_active is True

    asyncio.run(run_case())


def test_in_process_runner_can_terminate_teammate(tmp_path, monkeypatch):
    """Runner can terminate a running teammate."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        await asyncio.sleep(60)
        return "never reached"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="worker",
            agent_definition=definition,
            prompt="Long task",
            cwd=tmp_path,
        )
        # Give the task a moment to start
        await asyncio.sleep(0.05)
        terminated = await runner.terminate(result.agent_id)
        assert terminated is True
        record = agent_svc.get(result.agent_id)
        assert record.state == "cancelled"

    asyncio.run(run_case())


def test_in_process_runner_is_active_tracking(tmp_path, monkeypatch):
    """is_active returns True while task is running, False after completion."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        await asyncio.sleep(0.1)
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="worker",
            agent_definition=definition,
            prompt="Task",
            cwd=tmp_path,
        )
        assert runner.is_active(result.agent_id) is True
        await runner.wait(result.agent_id)
        assert runner.is_active(result.agent_id) is False

    asyncio.run(run_case())


def test_in_process_runner_sends_idle_notification_to_lead(tmp_path, monkeypatch):
    """Completed teammate sends idle notification to lead's mailbox."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("notify-test")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="researcher",
            agent_definition=definition,
            prompt="Research APIs",
            cwd=tmp_path,
        )
        await runner.wait(result.agent_id)

        # Check lead's mailbox for idle notification
        lead_mail = team_svc.read_mailbox(team_id, recipient="team-lead")
        idle_messages = [
            m for m in lead_mail if "idle" in m.content.lower() or "finished" in m.content.lower()
        ]
        assert (
            len(idle_messages) >= 1
        ), f"Expected idle notification in lead mailbox, got: {[m.content for m in lead_mail]}"

    asyncio.run(run_case())


def test_in_process_runner_registers_name_for_routing(tmp_path, monkeypatch):
    """Spawned teammate name is registered in AgentService for SendMessage routing."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return "done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("test-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="coder",
            agent_definition=definition,
            prompt="Write code",
            cwd=tmp_path,
        )
        # Verify the name was registered
        resolved_id = agent_svc.resolve_agent_id("coder")
        assert resolved_id == result.agent_id
        # Also verify get_by_name works
        record = agent_svc.get_by_name("coder")
        assert record is not None
        assert record.id == result.agent_id

    asyncio.run(run_case())


def test_in_process_runner_propagates_plan_mode_to_teammate_runtime(tmp_path, monkeypatch):
    """Plan-mode teammates should run under a plan-mode service and persist that mode."""

    from koder_agent.tools.plan_mode import _get_plan_service

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return f"plan_mode={_get_plan_service().mode}; prompt={prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("plan-team")
    definition = AgentDefinition(
        agent_type="planner",
        when_to_use="Planning work",
        system_prompt="Planner.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="planner-1",
            agent_definition=definition,
            prompt="Draft rollout plan",
            cwd=tmp_path,
            plan_mode_required=True,
        )
        await runner.wait(result.agent_id)
        await asyncio.sleep(0)
        record = agent_svc.get(result.agent_id)
        member = next(m for m in team_svc.member_records(team_id) if m.agent_id == result.agent_id)
        assert record.permission_mode == "plan"
        assert member.mode == "plan"
        assert Path(record.output_path).read_text(encoding="utf-8") == (
            "plan_mode=plan; prompt=Draft rollout plan"
        )

    asyncio.run(run_case())


def test_in_process_runner_consumes_live_mailbox_messages(tmp_path, monkeypatch):
    """Idle teammates should automatically pick up later mailbox messages."""

    prompts: list[str] = []

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        prompts.append(prompt)
        return f"done: {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("mailbox-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )

    async def wait_for_prompt_count(expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 2
        while len(prompts) < expected:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"Timed out waiting for {expected} prompts, saw {prompts}")
            await asyncio.sleep(0.05)

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="worker",
            agent_definition=definition,
            prompt="Initial task",
            cwd=tmp_path,
        )
        await runner.wait(result.agent_id)
        await asyncio.sleep(0)

        members = team_svc.member_records(team_id)
        worker = next(m for m in members if m.name == "worker")
        assert worker.is_active is True
        assert prompts == ["Initial task"]

        team_svc.route(
            team_id,
            "Follow-up request",
            recipient="worker",
            sender="team-lead",
        )
        await wait_for_prompt_count(2)
        await runner.wait(result.agent_id)

        assert prompts == ["Initial task", "Follow-up request"]
        mailbox = team_svc.mailbox_entries(team_id, recipient="worker")
        assert mailbox[-1].read is True

        terminated = await runner.terminate(result.agent_id)
        assert terminated is True
        await asyncio.sleep(0)

        members = team_svc.member_records(team_id)
        worker = next(m for m in members if m.name == "worker")
        assert worker.is_active is False

    asyncio.run(run_case())


def test_in_process_runner_routes_teammate_send_message_to_peer(tmp_path, monkeypatch):
    """A teammate's real send_message call should wake the addressed peer."""

    prompts: list[tuple[str | None, str]] = []

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        sender = team_context.sender_name if team_context is not None else None
        prompts.append((sender, prompt))
        if sender == "integrator" and prompt == "Ask critic directly":
            from koder_agent.harness.agents.teams.context import team_tool_context
            from koder_agent.tools.send_message import _send_message_impl

            with team_tool_context(team_context):
                result = await _send_message_impl(
                    to="critic",
                    message="DIRECT_PING_FROM_INTEGRATOR",
                    summary="Direct ping",
                )
            return f"integrator_result={result}"
        if sender == "critic" and prompt == "DIRECT_PING_FROM_INTEGRATOR":
            return "CRITIC_GOT_DIRECT_PING"
        return f"done: {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("peer-message-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )
    runner = InProcessTeammateRunner(agent_service=agent_svc, team_service=team_svc)

    async def wait_for_prompt(target: tuple[str | None, str]) -> None:
        deadline = asyncio.get_running_loop().time() + 2
        while target not in prompts:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"Timed out waiting for {target}: {prompts}")
            await asyncio.sleep(0.05)

    async def run_case():
        critic = await runner.spawn_teammate(
            team_id=team_id,
            name="critic",
            agent_definition=definition,
            prompt="Critic boot",
            cwd=tmp_path,
        )
        await runner.wait(critic.agent_id)

        integrator = await runner.spawn_teammate(
            team_id=team_id,
            name="integrator",
            agent_definition=definition,
            prompt="Ask critic directly",
            cwd=tmp_path,
        )

        await wait_for_prompt(("critic", "DIRECT_PING_FROM_INTEGRATOR"))
        await runner.wait(integrator.agent_id)
        await runner.wait(critic.agent_id)

        mailbox = team_svc.mailbox_entries(team_id, recipient="critic")
        assert mailbox[-1].sender == "integrator"
        assert mailbox[-1].read is True
        critic_output = Path(agent_svc.get(critic.agent_id).output_path).read_text(encoding="utf-8")
        integrator_output = Path(agent_svc.get(integrator.agent_id).output_path).read_text(
            encoding="utf-8"
        )
        assert critic_output == "CRITIC_GOT_DIRECT_PING"
        assert '"routing": "team_mailbox"' in integrator_output

        history = team_svc.history_entries(team_id)
        assert any(
            entry.event == "message_sent"
            and entry.sender == "integrator"
            and entry.recipient == "critic"
            and entry.content == "DIRECT_PING_FROM_INTEGRATOR"
            for entry in history
        )
        assert any(
            entry.event == "run_completed"
            and entry.member_name == "critic"
            and entry.content == "CRITIC_GOT_DIRECT_PING"
            for entry in history
        )

        await runner.terminate(critic.agent_id)
        await runner.terminate(integrator.agent_id)

    asyncio.run(run_case())


def test_in_process_runner_claims_and_completes_team_tasks_when_idle(tmp_path, monkeypatch):
    """Idle teammates should claim pending team tasks and complete them automatically."""

    prompts: list[str] = []

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        prompts.append(prompt)
        return f"done: {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService

    agent_svc = AgentService.for_test(tmp_path)
    team_svc = TeamService.for_test(root=tmp_path)
    team_id = team_svc.create_team("task-team")
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
    )

    runner = InProcessTeammateRunner(
        agent_service=agent_svc,
        team_service=team_svc,
    )
    task_service = team_svc.task_service(team_id)

    async def wait_for_prompt_count(expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 2
        while len(prompts) < expected:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"Timed out waiting for {expected} prompts, saw {prompts}")
            await asyncio.sleep(0.05)

    async def run_case():
        result = await runner.spawn_teammate(
            team_id=team_id,
            name="worker",
            agent_definition=definition,
            prompt="Boot worker",
            cwd=tmp_path,
        )
        await runner.wait(result.agent_id)
        await asyncio.sleep(0)

        task = task_service.create_task(
            "Review auth changes",
            active_form="Review the auth changes",
        )

        await wait_for_prompt_count(2)
        await runner.wait(result.agent_id)

        assert prompts == ["Boot worker", "Review the auth changes"]
        claimed = task_service.get_task(task.id)
        assert claimed is not None
        assert claimed.owner == result.agent_id
        assert claimed.status == "completed"

        await runner.terminate(result.agent_id)

    asyncio.run(run_case())
