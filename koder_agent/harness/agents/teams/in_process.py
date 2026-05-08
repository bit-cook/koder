"""In-process teammate runner -- executes teammates as asyncio tasks within the same process."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from koder_agent.harness.agents.definitions import AgentDefinition, resolve_agent_model
from koder_agent.harness.agents.service import AgentService
from koder_agent.harness.agents.teams.context import TeamToolContext
from koder_agent.harness.agents.teams.permission_bridge import PermissionBridge
from koder_agent.harness.agents.teams.service import TeamService
from koder_agent.harness.agents.teams.task_service import TeamTaskRecord

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 0.1
TEAM_LEAD_NAME = "team-lead"
LocalPromptExecutor = Callable[[str, TeamToolContext | None], Awaitable[str]]


@dataclass
class TeammateSpawnResult:
    """Result of spawning an in-process teammate."""

    agent_id: str
    name: str
    team_id: str


@dataclass
class _PendingTeammateWork:
    prompt: str
    source: str
    task_id: str | None = None


@dataclass
class _TeammateRuntime:
    team_id: str
    agent_id: str
    name: str
    agent_definition: AgentDefinition
    cwd: str
    current_run: asyncio.Task | None
    stop_event: asyncio.Event
    idle_event: asyncio.Event


class InProcessTeammateRunner:
    """Runs teammates as asyncio tasks within the current process.

    This is the 'in-process' backend for agent teams. Teammates share the same
    event loop and process as the leader, communicating via service-layer
    mailboxes and the shared task list.
    """

    def __init__(
        self,
        *,
        agent_service: AgentService,
        team_service: TeamService,
        permission_bridge: PermissionBridge | None = None,
        local_prompt_executor: LocalPromptExecutor | None = None,
    ):
        self._agent_service = agent_service
        self._team_service = team_service
        self._permission_bridge = permission_bridge
        self._local_prompt_executor = local_prompt_executor
        self._tasks: dict[str, asyncio.Task] = {}
        self._runtimes: dict[str, _TeammateRuntime] = {}

    def _local_executor_for(self, prompt: str):
        if self._local_prompt_executor is None or not prompt.lstrip().startswith("/"):
            return None

        async def execute_local(**kwargs: Any) -> str:
            return await self._local_prompt_executor(
                kwargs["prompt"],
                kwargs.get("team_context"),
            )

        return execute_local

    async def spawn_teammate(
        self,
        *,
        team_id: str,
        name: str,
        agent_definition: AgentDefinition,
        prompt: str,
        cwd: str | Path,
        plan_mode_required: bool = False,
        model: str | None = None,
    ) -> TeammateSpawnResult:
        """Spawn a teammate as an in-process background task.

        Registers the teammate as a team member, launches the agent
        via AgentService, and tracks the asyncio task for lifecycle mgmt.
        """
        permission_mode = (
            "plan" if plan_mode_required else (agent_definition.permission_mode or "default")
        )

        effective_model = model or resolve_agent_model(agent_definition) or agent_definition.model

        # Launch via AgentService
        record = await self._agent_service.launch_background(
            agent_definition=agent_definition,
            prompt=prompt,
            description=f"Teammate: {name}",
            cwd=cwd,
            permission_mode=permission_mode,
            team_context_builder=lambda record: TeamToolContext(
                team_id=team_id,
                sender_name=name,
                sender_agent_id=record.id,
                team_service=self._team_service,
                source="spawn",
            ),
            executor=self._local_executor_for(prompt),
        )

        # Register as team member
        self._team_service.add_member(
            team_id,
            record.id,
            name=name,
            agent_type=agent_definition.agent_type,
            model=effective_model,
            prompt=prompt,
            plan_mode_required=plan_mode_required,
            cwd=str(cwd),
            session_id=record.session_id,
            mode=permission_mode,
            is_active=True,
        )

        # Register name for SendMessage routing
        self._agent_service.register_name(name, record.id)

        runtime = _TeammateRuntime(
            team_id=team_id,
            agent_id=record.id,
            name=name,
            agent_definition=agent_definition,
            cwd=str(cwd),
            current_run=self._agent_service._tasks.get(record.id),
            stop_event=asyncio.Event(),
            idle_event=asyncio.Event(),
        )
        if runtime.current_run is None:
            runtime.idle_event.set()
        self._runtimes[record.id] = runtime
        self._tasks[record.id] = asyncio.create_task(self._run_teammate_loop(runtime))

        return TeammateSpawnResult(
            agent_id=record.id,
            name=name,
            team_id=team_id,
        )

    def _notify_teammate_idle(self, team_id: str, agent_id: str, name: str) -> None:
        """Dispatch idle hooks and notify the lead that the teammate is available again."""
        try:
            self._team_service.notify_member_idle(team_id, agent_id)
        except Exception:
            logger.debug("Failed to notify idle transition for %s", agent_id, exc_info=True)

        try:
            self._team_service.route(
                team_id,
                f"Teammate '{name}' has finished and is now idle.",
                recipient=TEAM_LEAD_NAME,
                sender=name,
            )
        except Exception:
            logger.debug("Failed to send idle notification for %s", agent_id, exc_info=True)

    def _sync_permission_mode(self, runtime: _TeammateRuntime) -> None:
        members = self._team_service.member_records(runtime.team_id)
        member = next((item for item in members if item.agent_id == runtime.agent_id), None)
        desired_mode = member.mode if member is not None else None
        if desired_mode is None:
            desired_mode = runtime.agent_definition.permission_mode or "default"
        record = self._agent_service.get(runtime.agent_id)
        if record.permission_mode != desired_mode:
            self._agent_service.update_permission_mode(runtime.agent_id, desired_mode)

    def _claim_next_task(self, runtime: _TeammateRuntime) -> TeamTaskRecord | None:
        task_service = self._team_service.task_service(runtime.team_id)
        for task in task_service.list_tasks():
            if task.status == "completed":
                continue
            claimed = task_service.claim_task(
                task.id,
                runtime.agent_id,
                check_agent_busy=True,
            )
            if claimed.success and claimed.task is not None:
                return claimed.task
        return None

    async def _wait_for_next_work(self, runtime: _TeammateRuntime) -> _PendingTeammateWork | None:
        while not runtime.stop_event.is_set():
            for recipient in dict.fromkeys((runtime.name, runtime.agent_id)):
                mailbox_entry = self._team_service.consume_next_mailbox_entry(
                    runtime.team_id,
                    recipient=recipient,
                )
                if mailbox_entry is not None:
                    return _PendingTeammateWork(
                        prompt=mailbox_entry.content,
                        source="mailbox",
                    )

            task = self._claim_next_task(runtime)
            if task is not None:
                return _PendingTeammateWork(
                    prompt=task.active_form or task.subject,
                    source="task",
                    task_id=task.id,
                )

            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
        return None

    async def _run_teammate_loop(self, runtime: _TeammateRuntime) -> None:
        """Keep an in-process teammate alive so it can accept follow-up work."""
        completed_task_id: str | None = None
        try:
            while not runtime.stop_event.is_set():
                if runtime.current_run is not None:
                    try:
                        await runtime.current_run
                    except asyncio.CancelledError:
                        if runtime.stop_event.is_set():
                            break
                        raise
                    finally:
                        runtime.current_run = None
                        runtime.idle_event.set()

                record = self._agent_service.get(runtime.agent_id)

                if completed_task_id is not None and record.state == "completed":
                    try:
                        self._team_service.task_service(runtime.team_id).update_status(
                            completed_task_id,
                            "completed",
                        )
                    except Exception:
                        logger.debug(
                            "Failed to complete claimed task %s for %s",
                            completed_task_id,
                            runtime.agent_id,
                            exc_info=True,
                        )
                    completed_task_id = None
                elif completed_task_id is not None and record.state != "in_progress":
                    completed_task_id = None

                if runtime.stop_event.is_set():
                    break

                if record.state in {"completed", "failed"}:
                    self._notify_teammate_idle(runtime.team_id, runtime.agent_id, runtime.name)

                next_work = await self._wait_for_next_work(runtime)
                if next_work is None:
                    continue

                self._sync_permission_mode(runtime)
                await self._agent_service.resume_background(
                    agent_id=runtime.agent_id,
                    agent_definition=runtime.agent_definition,
                    prompt=next_work.prompt,
                    cwd=runtime.cwd,
                    team_context=TeamToolContext(
                        team_id=runtime.team_id,
                        sender_name=runtime.name,
                        sender_agent_id=runtime.agent_id,
                        team_service=self._team_service,
                        source=next_work.source,
                    ),
                    executor=self._local_executor_for(next_work.prompt),
                )
                runtime.current_run = self._agent_service._tasks.get(runtime.agent_id)
                runtime.idle_event.clear()
                completed_task_id = next_work.task_id
        finally:
            try:
                self._team_service.set_member_active(runtime.team_id, runtime.agent_id, False)
            except Exception:
                logger.debug("Failed to deactivate teammate %s", runtime.agent_id, exc_info=True)
            runtime.idle_event.set()
            self._tasks.pop(runtime.agent_id, None)
            self._runtimes.pop(runtime.agent_id, None)

    async def wait(self, agent_id: str) -> None:
        """Wait for the teammate's current unit of work to finish."""
        runtime = self._runtimes.get(agent_id)
        if runtime is None:
            await self._agent_service.wait(agent_id)
            return
        await runtime.idle_event.wait()

    async def terminate(self, agent_id: str) -> bool:
        """Terminate a running or idle teammate loop."""
        runtime = self._runtimes.get(agent_id)
        if runtime is None:
            try:
                await self._agent_service.cancel_background(agent_id)
                return True
            except KeyError:
                return False

        runtime.stop_event.set()
        try:
            await self._agent_service.cancel_background(agent_id)
        except KeyError:
            return False
        loop_task = self._tasks.get(agent_id)
        if loop_task is not None:
            await loop_task
        return True

    def is_active(self, agent_id: str) -> bool:
        """Check if a teammate is currently busy processing work."""
        runtime = self._runtimes.get(agent_id)
        if runtime is None:
            return False
        return runtime.current_run is not None and not runtime.current_run.done()

    def manages(self, agent_id: str) -> bool:
        """Return whether this runner owns the given teammate lifecycle."""
        return agent_id in self._runtimes
