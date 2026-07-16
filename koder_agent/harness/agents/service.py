"""In-memory runtime agent lifecycle service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents import RunConfig, Runner

from koder_agent.agentic import create_dev_agent, get_display_hooks
from koder_agent.core.constants import get_max_turns
from koder_agent.core.session import EnhancedSQLiteSession
from koder_agent.harness.agents.hooks import SubagentLifecycleHooks
from koder_agent.harness.paths import worktrees_dir
from koder_agent.harness.plan.mode import PlanModeService
from koder_agent.harness.worktree.service import WorktreeService
from koder_agent.tools import get_all_tools
from koder_agent.tools.permission_context import (
    get_tool_permission_context,
    reset_tool_permission_context,
    subagent_permission_scope,
)
from koder_agent.tools.plan_mode import plan_service_scope
from koder_agent.tools.skill_context import skill_run_scope
from koder_agent.tools.todo import (
    TodoRuntimeIdentity,
    TodoStore,
    reset_todo_context,
    set_todo_context,
)
from koder_agent.utils.client import get_model_client_snapshot

from .definitions import (
    AgentDefinition,
    build_agent_system_prompt,
    filter_tools_for_agent_definition,
    resolve_agent_mcp_server_configs,
    resolve_agent_model,
)
from .messages import AgentMessage
from .models import AgentRecord, DelayedWorkerResult
from .summary import summarize_agent_record
from .teams.context import TeamToolContext, team_tool_context

logger = logging.getLogger(__name__)

AgentRunExecutor = Callable[..., Awaitable[str]]


async def _deny_approver(tool_name, arguments, decision):
    """Always-deny approver used for subagents.

    A subagent has no interactive terminal, so an approval-gated call must fail
    CLOSED. Passing approver=None instead would hit enforce_tool_permission's
    TTY-aware fallback, which fails OPEN in an interactive session. Returning
    "deny" is the verdict enforce_tool_permission understands as a block.
    """
    return "deny"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_provider_from_snapshot(snapshot: dict[str, Any]) -> str | None:
    kwargs = snapshot.get("litellm_kwargs") or {}
    litellm_model = str(kwargs.get("model") or "")
    if litellm_model:
        return litellm_model.split("/", 1)[0]
    model_name = str(snapshot.get("model_name") or "")
    if model_name.startswith("litellm/"):
        remainder = model_name[len("litellm/") :]
        return remainder.split("/", 1)[0] if remainder else None
    if snapshot.get("native_openai"):
        return "openai"
    return None


def _redacted_model_config_snapshot(agent_definition: AgentDefinition) -> dict[str, Any]:
    """Return safe model config evidence for runtime agent records."""

    model_override = resolve_agent_model(agent_definition)
    try:
        snapshot = get_model_client_snapshot(model_override)
    except Exception as exc:  # pragma: no cover - defensive config path
        return {
            "model_override": model_override or "inherit",
            "error": str(exc),
        }
    kwargs = snapshot.get("litellm_kwargs") or {}
    extra_headers = kwargs.get("extra_headers") or {}
    return {
        "model_override": model_override or "inherit",
        "model_name": snapshot.get("model_name"),
        "provider": _model_provider_from_snapshot(snapshot),
        "base_url": snapshot.get("base_url") or kwargs.get("base_url"),
        "native_openai": bool(snapshot.get("native_openai")),
        "api_key_present": bool(snapshot.get("api_key") or kwargs.get("api_key")),
        "reasoning_effort": snapshot.get("reasoning_effort"),
        "litellm_model": kwargs.get("model"),
        "oauth_provider": extra_headers.get("x-oauth-provider"),
        "oauth_headers_present": bool(extra_headers),
    }


def agent_definition_provenance(agent_definition: AgentDefinition) -> dict[str, Any]:
    """Return stable, non-secret evidence identifying an agent definition."""
    serialized = json.dumps(
        asdict(agent_definition),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "source": agent_definition.source,
        "filename": agent_definition.filename,
        "base_dir": agent_definition.base_dir,
        "plugin": agent_definition.plugin,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def agent_definition_matches_record(record: AgentRecord, agent_definition: AgentDefinition) -> bool:
    """Return whether a definition is exactly the one recorded for a run."""
    provenance = record.definition_provenance
    return isinstance(provenance, dict) and provenance == agent_definition_provenance(
        agent_definition
    )


def resolve_agent_record_origin(record: AgentRecord) -> Path:
    """Resolve a persisted origin cwd, rejecting missing or unsafe values."""
    raw = record.origin_cwd
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("background agent record has no originating directory")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("background agent originating directory must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("background agent originating directory is unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("background agent originating directory is not a directory")
    return resolved


def resolve_agent_record_execution_cwd(record: AgentRecord) -> Path:
    """Resolve the persisted worktree or originating cwd used for execution."""
    origin = resolve_agent_record_origin(record)
    if not record.worktree_path:
        return origin
    worktree = Path(record.worktree_path).expanduser()
    if not worktree.is_absolute():
        raise ValueError("background agent worktree directory must be absolute")
    try:
        resolved = worktree.resolve(strict=True)
    except OSError as exc:
        raise ValueError("background agent worktree directory is unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("background agent worktree directory is not a directory")
    return resolved


async def _cleanup_agent_mcp_servers(
    agent: Any,
    *,
    propagate_cancellation: bool = True,
) -> None:
    from koder_agent.mcp import close_mcp_servers, detach_mcp_server_owner

    owner = detach_mcp_server_owner(agent)
    await close_mcp_servers(
        owner,
        propagate_cancellation=propagate_cancellation,
    )


async def _execute_agent_run(
    *,
    agent_definition: AgentDefinition,
    prompt: str,
    session_id: str,
    seed_items: list[dict[str, Any]] | None,
    cwd: str | None,
    team_context: TeamToolContext | None = None,
    permission_service: Any = None,
    todo_store: TodoStore | None = None,
) -> str:
    if todo_store is None:
        todo_store = TodoStore(
            TodoRuntimeIdentity(
                session_id=session_id,
                agent_id=agent_definition.agent_type,
                run_id=session_id,
            )
        )

    tools = filter_tools_for_agent_definition(agent_definition, get_all_tools())
    # Subagents cannot spawn other subagents
    tools = [tool for tool in tools if tool.name not in {"task_delegate", "agent_tool"}]
    agent = None
    perm_token = None
    todo_token = None
    propagate_cleanup_cancellation = True
    try:
        agent = await create_dev_agent(
            tools,
            name=agent_definition.agent_type,
            instructions_override=build_agent_system_prompt(
                agent_definition, cwd=cwd or Path.cwd()
            ),
            model_override=resolve_agent_model(agent_definition),
            extra_mcp_server_configs=resolve_agent_mcp_server_configs(agent_definition),
        )
        session = EnhancedSQLiteSession(session_id=session_id)
        if seed_items:
            existing_items = await session.get_items()
            if not existing_items:
                await session.add_items(seed_items)

        if todo_store.identity.session_id != session_id:
            raise ValueError("todo store session identity does not match subagent session")
        perm_token = subagent_permission_scope(permission_service, deny_approver=_deny_approver)
        todo_token = set_todo_context(todo_store)
        perm_ctx = get_tool_permission_context()
        effective_service = perm_ctx.permission_service if perm_ctx else None
        with team_tool_context(team_context):
            lifecycle_hooks = SubagentLifecycleHooks(
                agent_definition=agent_definition,
                cwd=cwd or Path.cwd(),
                wrapped_hooks=get_display_hooks(),
                permission_service=effective_service,
            )
            with skill_run_scope(lifecycle_hooks) as run_hooks:
                result = await Runner.run(
                    agent,
                    prompt,
                    session=session,
                    run_config=RunConfig(),
                    hooks=run_hooks,
                    max_turns=agent_definition.max_turns or get_max_turns(),
                )
    except BaseException:
        propagate_cleanup_cancellation = False
        raise
    finally:
        if todo_token is not None:
            reset_todo_context(todo_token)
        if perm_token is not None:
            reset_tool_permission_context(perm_token)
        if agent is not None:
            await _cleanup_agent_mcp_servers(
                agent,
                propagate_cancellation=propagate_cleanup_cancellation,
            )
    return str(result.final_output)


class AgentService:
    """Stable service for spawning agents and routing mailbox messages."""

    def __init__(self, *, output_root: Path | None = None, permission_service: Any = None):
        self._agents: dict[str, AgentRecord] = {}
        self._mailboxes: dict[str, list[AgentMessage]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._todo_stores: dict[TodoRuntimeIdentity, TodoStore] = {}
        self._todo_identities_by_agent: dict[str, TodoRuntimeIdentity] = {}
        self._name_registry: dict[str, str] = {}  # name -> agent_id
        self._owned_temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._permission_service = permission_service
        self.output_root = (output_root or (Path.home() / ".koder" / "agents")).expanduser()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._load_records()

    @classmethod
    def for_test(cls, root: Path | None = None) -> "AgentService":
        if root is not None:
            return cls(output_root=root / "agent-output")
        temp_dir = tempfile.TemporaryDirectory(prefix="koder-agent-output-")
        service = cls(output_root=Path(temp_dir.name) / "agent-output")
        service._owned_temp_dir = temp_dir
        return service

    def close(self) -> None:
        self._todo_stores.clear()
        self._todo_identities_by_agent.clear()
        if self._owned_temp_dir is not None:
            self._owned_temp_dir.cleanup()
            self._owned_temp_dir = None

    def __del__(self) -> None:
        self.close()

    def spawn(self, profile: str) -> str:
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        record = AgentRecord.create(agent_id=agent_id, profile=profile)
        self._agents[agent_id] = self._with_summary(record)
        self._mailboxes[agent_id] = []
        self._save_record(self._agents[agent_id])
        return agent_id

    def get(self, agent_id: str) -> AgentRecord:
        return self._agents[agent_id]

    def list_records(self) -> list[AgentRecord]:
        return sorted(self._agents.values(), key=lambda item: item.updated_at, reverse=True)

    def refresh_summary(self, agent_id: str) -> AgentRecord:
        record = self.get(agent_id)
        updated = self._with_summary(record, output_text=self._read_output(record))
        self._agents[agent_id] = updated
        self._save_record(updated)
        return updated

    def send(self, agent_id: str, content: str) -> AgentMessage:
        message = AgentMessage.create(agent_id=agent_id, content=content)
        self._mailboxes[agent_id].append(message)
        return message

    def read_mailbox(self, agent_id: str) -> list[AgentMessage]:
        return list(self._mailboxes[agent_id])

    def mark_worker_delayed(self, agent_id: str) -> DelayedWorkerResult:
        agent = self.get(agent_id)
        self._agents[agent_id] = replace(agent, state="delayed", updated_at=_utc_now_iso())
        self._save_record(self._agents[agent_id])
        return DelayedWorkerResult(agent_id=agent_id, state_preserved=True)

    async def launch_background(
        self,
        *,
        agent_definition: AgentDefinition,
        prompt: str,
        description: str,
        seed_items: list[dict[str, Any]] | None = None,
        cwd: str | Path | None = None,
        permission_mode: str | None = None,
        team_context_builder: Callable[[AgentRecord], TeamToolContext | None] | None = None,
        executor: AgentRunExecutor | None = None,
    ) -> AgentRecord:
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        session_id = f"subagent-{agent_id}"
        output_path = self.output_root / f"{agent_id}.output"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        worktree_path = None
        worktree_branch = None
        origin_cwd = Path(cwd) if cwd is not None else Path.cwd()
        origin_cwd = origin_cwd.expanduser().resolve(strict=True)
        if not origin_cwd.is_dir():
            raise ValueError("background agent cwd must be a directory")
        effective_cwd = str(origin_cwd)
        if agent_definition.isolation == "worktree" and cwd is not None:
            cwd_path = origin_cwd
            service = WorktreeService(worktrees_dir(cwd_path), repo_root=cwd_path)
            created = service.create(f"agent/{agent_id}")
            worktree_path = str(created.path)
            worktree_branch = created.branch
            effective_cwd = worktree_path

        record = AgentRecord.create(
            agent_id=agent_id,
            profile=agent_definition.agent_type,
            session_id=session_id,
            description=description,
            prompt=prompt,
            output_path=str(output_path),
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
            permission_mode=permission_mode or agent_definition.permission_mode,
            state="in_progress",
            model_config=_redacted_model_config_snapshot(agent_definition),
            origin_cwd=str(origin_cwd),
            definition_provenance=agent_definition_provenance(agent_definition),
        )
        record = self._with_summary(record)
        self._agents[agent_id] = record
        self._mailboxes[agent_id] = []
        self._save_record(record)
        team_context = team_context_builder(record) if team_context_builder is not None else None
        self._tasks[agent_id] = asyncio.create_task(
            self._run_background(
                agent_id=agent_id,
                agent_definition=agent_definition,
                prompt=prompt,
                session_id=session_id,
                seed_items=seed_items,
                cwd=effective_cwd,
                team_context=team_context,
                executor=executor,
                todo_store=self._get_or_create_todo_store(agent_id, session_id),
            )
        )
        return record

    async def run_sync(
        self,
        *,
        agent_definition: AgentDefinition,
        prompt: str,
        seed_items: list[dict[str, Any]] | None = None,
        cwd: str | Path | None = None,
        permission_mode: str | None = None,
    ) -> str:
        effective_cwd = str(cwd) if cwd is not None else None
        worktree_service: WorktreeService | None = None
        worktree_created = None
        if agent_definition.isolation == "worktree" and cwd is not None:
            cwd_path = Path(cwd).resolve()
            worktree_service = WorktreeService(worktrees_dir(cwd_path), repo_root=cwd_path)
            worktree_created = worktree_service.create(f"sync-agent/{uuid.uuid4().hex[:8]}")
            effective_cwd = str(worktree_created.path)
        try:
            scoped_service = PlanModeService()
            effective_permission_mode = (
                permission_mode or agent_definition.permission_mode or "default"
            )
            if effective_permission_mode == "plan":
                scoped_service.enter_plan_mode(permission_mode="plan")
            with plan_service_scope(scoped_service):
                session_id = f"subagent-sync-{uuid.uuid4().hex[:8]}"
                return await _execute_agent_run(
                    agent_definition=agent_definition,
                    prompt=prompt,
                    session_id=session_id,
                    seed_items=seed_items,
                    cwd=effective_cwd,
                    permission_service=self._permission_service,
                    todo_store=TodoStore(
                        TodoRuntimeIdentity(
                            session_id=session_id,
                            agent_id=f"sync-{agent_definition.agent_type}",
                            run_id=session_id,
                        )
                    ),
                )
        finally:
            # Dirty worktrees are kept so the user can inspect or merge them.
            if worktree_service is not None and worktree_created is not None:
                try:
                    worktree_service.remove_if_clean(
                        worktree_created.path, branch=worktree_created.branch
                    )
                except Exception:
                    logger.debug(
                        "Worktree cleanup failed for %s", worktree_created.path, exc_info=True
                    )

    async def resume_background(
        self,
        *,
        agent_id: str,
        agent_definition: AgentDefinition,
        prompt: str,
        cwd: str | Path | None = None,
        team_context: TeamToolContext | None = None,
        executor: AgentRunExecutor | None = None,
    ) -> AgentRecord:
        record = self.get(agent_id)
        if agent_id in self._tasks and not self._tasks[agent_id].done():
            raise RuntimeError(f"Agent is still running: {agent_id}")
        if not agent_definition_matches_record(record, agent_definition):
            raise ValueError("background agent definition provenance does not match")
        execution_cwd = resolve_agent_record_execution_cwd(record)
        if cwd is not None:
            requested_cwd = Path(cwd).expanduser().resolve(strict=True)
            if requested_cwd != execution_cwd:
                raise ValueError("background agent resume cwd does not match persisted origin")
        timestamp = _utc_now_iso()
        updated = replace(
            record,
            prompt=prompt,
            permission_mode=record.permission_mode,
            state="in_progress",
            error=None,
            model_config=_redacted_model_config_snapshot(agent_definition),
            updated_at=timestamp,
        )
        self._agents[agent_id] = self._with_summary(
            updated, summary_timestamp=timestamp, record_timestamp=timestamp
        )
        self._save_record(self._agents[agent_id])
        self._tasks[agent_id] = asyncio.create_task(
            self._run_background(
                agent_id=agent_id,
                agent_definition=agent_definition,
                prompt=prompt,
                session_id=record.session_id,
                seed_items=None,
                cwd=str(execution_cwd),
                team_context=team_context,
                executor=executor,
                todo_store=self._get_or_create_todo_store(agent_id, record.session_id),
            )
        )
        return self._agents[agent_id]

    async def wait(self, agent_id: str) -> AgentRecord:
        task = self._tasks.get(agent_id)
        if task is not None:
            await task
        return self.get(agent_id)

    async def cancel_background(self, agent_id: str) -> AgentRecord:
        task = self._tasks.get(agent_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        record = self.get(agent_id)
        timestamp = _utc_now_iso()
        updated = replace(record, state="cancelled", updated_at=timestamp)
        updated = self._with_summary(
            updated,
            output_text="Cancelled",
            summary_timestamp=timestamp,
            record_timestamp=timestamp,
        )
        self._agents[agent_id] = updated
        self._save_record(updated)
        if updated.output_path:
            Path(updated.output_path).write_text("Cancelled", encoding="utf-8")
        return updated

    def _cleanup_clean_worktree(self, record: AgentRecord) -> bool:
        """Remove a completed agent's isolation worktree when it has no changes.

        Dispatches ``WorktreeRemove`` hooks via WorktreeService.exit(). Dirty
        worktrees are kept so the user can inspect or merge the agent's work.
        Returns True when the worktree was removed.
        """
        if not record.worktree_path:
            return False
        path = Path(record.worktree_path)
        repo_root = None
        # The worktree lives under <repo>/.koder/worktrees/<slug>; walk up to
        # find the owning repository root.
        for parent in path.parents:
            if (parent / ".git").exists():
                repo_root = parent
                break
        try:
            service = WorktreeService(path.parent, repo_root=repo_root)
            return service.remove_if_clean(path, branch=record.worktree_branch)
        except Exception:
            logger.debug("Worktree cleanup failed for %s", record.worktree_path, exc_info=True)
            return False

    async def _run_background(
        self,
        *,
        agent_id: str,
        agent_definition: AgentDefinition,
        prompt: str,
        session_id: str,
        seed_items: list[dict[str, Any]] | None,
        cwd: str | None,
        team_context: TeamToolContext | None = None,
        executor: AgentRunExecutor | None = None,
        todo_store: TodoStore,
    ) -> None:
        record = self.get(agent_id)
        output_path = Path(record.output_path) if record.output_path else None
        try:
            scoped_service = PlanModeService()
            effective_permission_mode = (
                record.permission_mode or agent_definition.permission_mode or "default"
            )
            if effective_permission_mode == "plan":
                scoped_service.enter_plan_mode(permission_mode="plan")
            with plan_service_scope(scoped_service):
                execute_kwargs: dict[str, Any] = {
                    "agent_definition": agent_definition,
                    "prompt": prompt,
                    "session_id": session_id,
                    "seed_items": seed_items,
                    "cwd": cwd,
                    "permission_service": self._permission_service,
                    "todo_store": todo_store,
                }
                if team_context is not None:
                    execute_kwargs["team_context"] = team_context
                todo_token = set_todo_context(todo_store)
                try:
                    if executor is None:
                        result = await _execute_agent_run(**execute_kwargs)
                    else:
                        result = await executor(**execute_kwargs)
                finally:
                    reset_todo_context(todo_token)
            if output_path is not None:
                output_path.write_text(result, encoding="utf-8")
            self._record_team_run_history(
                team_context=team_context,
                prompt=prompt,
                output=result,
                state="completed",
            )
            timestamp = _utc_now_iso()
            worktree_removed = self._cleanup_clean_worktree(record)
            updated = replace(
                self.get(agent_id),
                state="completed",
                error=None,
                updated_at=timestamp,
                worktree_path=None if worktree_removed else record.worktree_path,
            )
            self._agents[agent_id] = self._with_summary(
                updated,
                output_text=result,
                summary_timestamp=timestamp,
                record_timestamp=timestamp,
            )
            self._save_record(self._agents[agent_id])
        except asyncio.CancelledError:
            if output_path is not None:
                output_path.write_text("Cancelled", encoding="utf-8")
            self._record_team_run_history(
                team_context=team_context,
                prompt=prompt,
                output="Cancelled",
                state="cancelled",
            )
            timestamp = _utc_now_iso()
            updated = replace(
                self.get(agent_id),
                state="cancelled",
                error=None,
                updated_at=timestamp,
            )
            self._agents[agent_id] = self._with_summary(
                updated,
                output_text="Cancelled",
                summary_timestamp=timestamp,
                record_timestamp=timestamp,
            )
            self._save_record(self._agents[agent_id])
            raise
        except Exception as exc:  # pragma: no cover - defensive runtime path
            output_text = f"Error: {exc}"
            if output_path is not None:
                output_path.write_text(output_text, encoding="utf-8")
            self._record_team_run_history(
                team_context=team_context,
                prompt=prompt,
                output=output_text,
                state="failed",
            )
            timestamp = _utc_now_iso()
            updated = replace(
                self.get(agent_id),
                state="failed",
                error=str(exc),
                updated_at=timestamp,
            )
            self._agents[agent_id] = self._with_summary(
                updated,
                output_text=output_text,
                summary_timestamp=timestamp,
                record_timestamp=timestamp,
            )
            self._save_record(self._agents[agent_id])

    def _record_team_run_history(
        self,
        *,
        team_context: TeamToolContext | None,
        prompt: str,
        output: str,
        state: str,
    ) -> None:
        if team_context is None:
            return
        try:
            team_context.team_service.record_run(
                team_context.team_id,
                agent_id=team_context.sender_agent_id,
                member_name=team_context.sender_name,
                prompt=prompt,
                output=output,
                state=state,
                source=team_context.source,
            )
        except Exception:
            logger.debug("Failed to record team run history", exc_info=True)

    def register_name(self, name: str, agent_id: str) -> None:
        """Register a human-readable name for an agent, making it addressable."""
        self._name_registry[name] = agent_id

    def release_agent(self, agent_id: str) -> None:
        """Release in-memory state owned by an explicitly cleaned-up agent."""
        task = self._tasks.get(agent_id)
        if task is not None and not task.done():
            raise RuntimeError(f"Agent is still running: {agent_id}")
        identity = self._todo_identities_by_agent.pop(agent_id, None)
        if identity is not None:
            self._todo_stores.pop(identity, None)

    def _get_or_create_todo_store(self, agent_id: str, session_id: str) -> TodoStore:
        identity = TodoRuntimeIdentity(
            session_id=session_id,
            agent_id=agent_id,
            run_id=f"agent-conversation:{agent_id}",
        )
        previous = self._todo_identities_by_agent.get(agent_id)
        if previous is not None and previous != identity:
            self._todo_stores.pop(previous, None)
        self._todo_identities_by_agent[agent_id] = identity
        store = self._todo_stores.get(identity)
        if store is None:
            store = TodoStore(identity)
            self._todo_stores[identity] = store
        return store

    def update_permission_mode(self, agent_id: str, permission_mode: str) -> AgentRecord:
        """Persist an updated permission mode for an existing agent record."""

        record = self.get(agent_id)
        updated = replace(record, permission_mode=permission_mode, updated_at=_utc_now_iso())
        self._agents[agent_id] = updated
        self._save_record(updated)
        return updated

    def get_by_name(self, name: str) -> AgentRecord | None:
        """Look up an agent by registered name."""
        agent_id = self._name_registry.get(name)
        if agent_id is None:
            return None
        try:
            return self.get(agent_id)
        except KeyError:
            return None

    def resolve_agent_id(self, name_or_id: str) -> str | None:
        """Resolve a name or agent_id to an agent_id."""
        if name_or_id in self._agents:
            return name_or_id
        return self._name_registry.get(name_or_id)

    def _record_path(self, agent_id: str) -> Path:
        return self.output_root / f"{agent_id}.json"

    def _save_record(self, record: AgentRecord) -> None:
        self._record_path(record.id).write_text(
            json.dumps(asdict(record), ensure_ascii=False),
            encoding="utf-8",
        )

    def _read_output(self, record: AgentRecord) -> str | None:
        if not record.output_path:
            return None
        try:
            return Path(record.output_path).read_text(encoding="utf-8")
        except OSError:
            return None

    def _with_summary(
        self,
        record: AgentRecord,
        *,
        output_text: str | None = None,
        summary_timestamp: str | None = None,
        record_timestamp: str | None = None,
    ) -> AgentRecord:
        timestamp = summary_timestamp or _utc_now_iso()
        return replace(
            record,
            summary=summarize_agent_record(record, output_text=output_text),
            summary_updated_at=timestamp,
            updated_at=record_timestamp or record.updated_at,
        )

    def _load_records(self) -> None:
        for path in sorted(self.output_root.glob("agent-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if "permission_mode" not in data:
                    data["permission_mode"] = None
                if "summary" not in data:
                    data["summary"] = None
                if "summary_updated_at" not in data:
                    data["summary_updated_at"] = None
                if "model_config" not in data:
                    data["model_config"] = None
                if "origin_cwd" not in data:
                    data["origin_cwd"] = None
                if "definition_provenance" not in data:
                    data["definition_provenance"] = None
                record = AgentRecord(**data)
            except Exception:
                logger.debug("Failed to parse agent record from file", exc_info=True)
                continue
            if not record.summary:
                record = self._with_summary(
                    record,
                    output_text=self._read_output(record),
                    summary_timestamp=record.updated_at,
                    record_timestamp=record.updated_at,
                )
            self._agents[record.id] = record
            self._mailboxes.setdefault(record.id, [])
