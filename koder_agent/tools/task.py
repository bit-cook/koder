"""Task delegation operations."""

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, List, Union

from agents import RunConfig, Runner
from pydantic import BaseModel, Field

from ..core.constants import get_max_turns
from ..harness.config.task_delegate_limits import (
    DEFAULT_TASK_DELEGATE_BATCH_SIZE,
    HARD_MAX_TASK_DELEGATE_BATCH_SIZE,
    TASK_DELEGATE_MAX_BATCH_SIZE_ENV,
    TASK_DELEGATE_MAX_CONCURRENCY_ENV,
    parse_task_delegate_limit,
)
from ..harness.config.task_delegate_limits import (
    DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY as DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY,
)
from .compat import function_tool
from .skill_context import skill_run_scope

TASK_DELEGATE_CHILD_RESULT_MAX_CHARS = 10_000
TASK_DELEGATE_AGGREGATE_MAX_CHARS = 30_000
TASK_DELEGATE_DESCRIPTION_MAX_CHARS = 500


class TaskModel(BaseModel):
    description: str
    prompt: str
    agent_type: str | None = None


TaskBatch = Annotated[
    List[TaskModel],
    Field(min_length=1, max_length=HARD_MAX_TASK_DELEGATE_BATCH_SIZE),
]
TaskInput = Union[TaskBatch, TaskModel]


class TaskDelegateModel(BaseModel):
    tasks: TaskInput


@dataclass(frozen=True)
class TaskDelegateLimits:
    max_batch_size: int
    max_concurrency: int


def resolve_task_delegate_max_batch_size() -> int:
    """Resolve the effective accepted task delegation batch size."""
    env_value = os.environ.get(TASK_DELEGATE_MAX_BATCH_SIZE_ENV)
    if env_value is not None:
        return parse_task_delegate_limit(
            env_value,
            source=TASK_DELEGATE_MAX_BATCH_SIZE_ENV,
        )

    from ..harness.config.service import RuntimeConfigService

    configured = RuntimeConfigService().load().harness.task_delegate_max_batch_size
    return parse_task_delegate_limit(
        configured,
        source="harness.task_delegate_max_batch_size",
    )


def resolve_task_delegate_limits() -> TaskDelegateLimits:
    """Resolve and validate effective batch and concurrency limits."""
    from ..harness.config.service import RuntimeConfigService

    harness_config = RuntimeConfigService().load().harness
    batch_value = os.environ.get(
        TASK_DELEGATE_MAX_BATCH_SIZE_ENV,
        harness_config.task_delegate_max_batch_size,
    )
    concurrency_value = os.environ.get(
        TASK_DELEGATE_MAX_CONCURRENCY_ENV,
        harness_config.task_delegate_max_concurrency,
    )
    batch_source = (
        TASK_DELEGATE_MAX_BATCH_SIZE_ENV
        if TASK_DELEGATE_MAX_BATCH_SIZE_ENV in os.environ
        else "harness.task_delegate_max_batch_size"
    )
    concurrency_source = (
        TASK_DELEGATE_MAX_CONCURRENCY_ENV
        if TASK_DELEGATE_MAX_CONCURRENCY_ENV in os.environ
        else "harness.task_delegate_max_concurrency"
    )
    max_batch_size = parse_task_delegate_limit(batch_value, source=batch_source)
    max_concurrency = parse_task_delegate_limit(
        concurrency_value,
        source=concurrency_source,
    )
    if max_concurrency > max_batch_size:
        raise ValueError(
            "Invalid task delegation configuration: "
            f"{concurrency_source} ({max_concurrency}) must be less than or equal to "
            f"{batch_source} ({max_batch_size})"
        )
    return TaskDelegateLimits(
        max_batch_size=max_batch_size,
        max_concurrency=max_concurrency,
    )


def _bounded_report_text(value: object, max_chars: int, *, label: str) -> str:
    """Bound report text with exact original/omitted character metadata."""
    text = value if isinstance(value, str) else str(value)
    total = len(text)
    if total <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    marker = f"\n...[{label} truncated: original {total} characters]...\n"
    for _ in range(3):
        content_budget = max(0, max_chars - len(marker))
        omitted = total - content_budget
        updated = f"\n...[{label} truncated: omitted {omitted} of {total} characters]...\n"
        if updated == marker:
            break
        marker = updated

    if len(marker) >= max_chars:
        return marker[:max_chars]
    content_budget = max_chars - len(marker)
    head_length = (content_budget * 7) // 10
    tail_length = content_budget - head_length
    tail = text[-tail_length:] if tail_length else ""
    return "".join((text[:head_length], marker, tail))


def _result_budgets(display_descriptions: list[str]) -> list[int]:
    """Allocate bounded child-result space while retaining every task section."""
    if len(display_descriptions) == 1:
        prefix = f"Delegated task '{display_descriptions[0]}' completed successfully:\n\n"
        return [
            min(
                TASK_DELEGATE_CHILD_RESULT_MAX_CHARS,
                max(0, TASK_DELEGATE_AGGREGATE_MAX_CHARS - len(prefix)),
            )
        ]

    report_header = "# Delegated Tasks Results\n\n"
    section_prefixes = [
        f"## Task {index}: {description}\n\n"
        for index, description in enumerate(display_descriptions, 1)
    ]
    fixed_size = len(report_header) + sum(len(prefix) + 2 for prefix in section_prefixes)
    available = max(0, TASK_DELEGATE_AGGREGATE_MAX_CHARS - fixed_size)
    base, remainder = divmod(available, len(display_descriptions))
    return [
        min(TASK_DELEGATE_CHILD_RESULT_MAX_CHARS, base + (index < remainder))
        for index in range(len(display_descriptions))
    ]


async def _drain_child_tasks(child_tasks: list[asyncio.Task]) -> None:
    """Wait for child cancellation and cleanup despite repeated parent cancellation."""
    drain = asyncio.gather(*child_tasks, return_exceptions=True)
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            continue
    drain.result()


async def _task_delegate_impl(tasks: TaskInput) -> str:
    """Delegate one or more tasks and return their input-ordered results."""
    from .todo import (
        TodoRuntimeIdentity,
        TodoStore,
        get_todo_store_or_none,
        reset_todo_context,
        set_todo_context,
    )

    direct_todo_token = None
    if get_todo_store_or_none() is None:
        direct_todo_token = set_todo_context(
            TodoStore(
                TodoRuntimeIdentity(
                    session_id="__direct__",
                    agent_id="task_delegate",
                    run_id=f"task-delegate-{uuid.uuid4().hex}",
                )
            )
        )

    try:
        limits = resolve_task_delegate_limits()
        task_list = [tasks] if isinstance(tasks, TaskModel) else list(tasks)

        if not task_list:
            raise ValueError("task_delegate requires at least one task")
        if len(task_list) > limits.max_batch_size:
            raise ValueError(
                f"task_delegate batch size {len(task_list)} exceeds the configured "
                f"maximum of {limits.max_batch_size}"
            )

        display_descriptions = [
            _bounded_report_text(
                task.description,
                TASK_DELEGATE_DESCRIPTION_MAX_CHARS,
                label="task description",
            )
            for task in task_list
        ]
        result_budgets = _result_budgets(display_descriptions)
        semaphore = asyncio.Semaphore(limits.max_concurrency)

        async def run_single_task(index: int, task: TaskModel) -> tuple[str, str]:
            """Run a single task and return (description, result)."""
            description = display_descriptions[index]
            result_budget = result_budgets[index]
            async with semaphore:
                delegated_agent = None
                permission_token = None
                todo_token = None
                propagate_cleanup_cancellation = True
                try:
                    try:
                        from ..agentic import create_dev_agent, get_display_hooks
                        from ..harness.agents.definitions import (
                            build_agent_system_prompt,
                            filter_tools_for_agent_definition,
                            get_agent_definitions,
                            resolve_agent_mcp_server_configs,
                            resolve_agent_model,
                        )
                        from ..harness.agents.service import (
                            _cleanup_agent_mcp_servers,
                            _deny_approver,
                        )
                        from . import get_all_tools
                        from .permission_context import (
                            reset_tool_permission_context,
                            subagent_permission_scope,
                        )
                        from .todo import get_todo_store

                        agent_definitions = get_agent_definitions(cwd=Path.cwd())
                        selected_agent = None
                        if task.agent_type:
                            selected_agent = next(
                                (
                                    agent
                                    for agent in agent_definitions.active_agents
                                    if agent.agent_type == task.agent_type
                                ),
                                None,
                            )
                            if selected_agent is None:
                                return description, _bounded_report_text(
                                    f"Error: unknown agent type {task.agent_type}",
                                    result_budget,
                                    label="task result",
                                )

                        tools = [tool for tool in get_all_tools() if tool.name != "task_delegate"]
                        if selected_agent is not None:
                            tools = filter_tools_for_agent_definition(selected_agent, tools)

                        delegated_agent = await create_dev_agent(
                            tools,
                            name=(
                                selected_agent.agent_type
                                if selected_agent
                                else f"Delegated Agent - {task.description[:30]}..."
                            ),
                            instructions_override=(
                                build_agent_system_prompt(selected_agent, cwd=Path.cwd())
                                if selected_agent is not None
                                else f"""You are a task agent handling this task: {task.description}

You have access to tools to help complete this task effectively.
Be concise and focused on the specific task at hand.
Return your findings or results directly without unnecessary explanation."""
                            ),
                            model_override=resolve_agent_model(selected_agent),
                            extra_mcp_server_configs=(
                                resolve_agent_mcp_server_configs(selected_agent)
                                if selected_agent is not None
                                else None
                            ),
                        )

                        permission_token = subagent_permission_scope(deny_approver=_deny_approver)
                        parent_identity = get_todo_store().identity
                        todo_token = set_todo_context(
                            TodoStore(
                                TodoRuntimeIdentity(
                                    session_id=parent_identity.session_id,
                                    agent_id=(
                                        selected_agent.agent_type
                                        if selected_agent
                                        else "delegated-task"
                                    ),
                                    run_id=f"task-{uuid.uuid4().hex}",
                                )
                            )
                        )
                        try:
                            if selected_agent is not None and selected_agent.max_turns:
                                max_turns = selected_agent.max_turns
                            else:
                                max_turns = get_max_turns()
                            with skill_run_scope(get_display_hooks()) as run_hooks:
                                result = await Runner.run(
                                    delegated_agent,
                                    task.prompt,
                                    max_turns=max_turns,
                                    run_config=RunConfig(),
                                    hooks=run_hooks,
                                )
                        finally:
                            if todo_token is not None:
                                reset_todo_context(todo_token)
                                todo_token = None
                            if permission_token is not None:
                                reset_tool_permission_context(permission_token)
                                permission_token = None

                        outcome = (
                            description,
                            _bounded_report_text(
                                result.final_output,
                                result_budget,
                                label="task result",
                            ),
                        )

                    except Exception as exc:
                        outcome = (
                            description,
                            _bounded_report_text(
                                f"Error: {exc}",
                                result_budget,
                                label="task result",
                            ),
                        )
                except BaseException:
                    propagate_cleanup_cancellation = False
                    raise
                finally:
                    if todo_token is not None:
                        reset_todo_context(todo_token)
                    if permission_token is not None:
                        reset_tool_permission_context(permission_token)
                    if delegated_agent is not None:
                        await _cleanup_agent_mcp_servers(
                            delegated_agent,
                            propagate_cancellation=propagate_cleanup_cancellation,
                        )
                return outcome

        child_tasks = [
            asyncio.create_task(run_single_task(index, task), name=f"task-delegate:{index}")
            for index, task in enumerate(task_list)
        ]
        try:
            results = await asyncio.gather(*child_tasks)
        except BaseException:
            for child_task in child_tasks:
                if not child_task.done() and child_task.cancelling() == 0:
                    child_task.cancel()
            await _drain_child_tasks(child_tasks)
            raise

        if len(results) == 1:
            description, result = results[0]
            return f"Delegated task '{description}' completed successfully:\n\n{result}"

        report_parts = ["# Delegated Tasks Results\n\n"]
        for index, (description, result) in enumerate(results, 1):
            report_parts.extend((f"## Task {index}: {description}\n\n", result, "\n\n"))
        return "".join(report_parts)

    except Exception as exc:
        return f"Error delegating tasks: {exc}"
    finally:
        if direct_todo_token is not None:
            reset_todo_context(direct_todo_token)


@function_tool
async def task_delegate(tasks: TaskInput) -> str:
    """Delegate one or more tasks to autonomous sub-agents and return their results.

    Multiple tasks run in parallel. Each task needs a short description and a
    self-contained prompt: the sub-agent cannot see this conversation, so
    include every file path, constraint, and expected output format it needs.

    Never delegate understanding. The prompt must prove you already understand
    the problem - name the exact files, line numbers, and what to change or
    find. Brief the agent like a smart colleague who just walked in: for
    lookups, hand over the exact command to run; for investigations, hand over
    the precise question to answer.

    When to use: explorations likely to take more than 3 search/read queries,
    or independent subtasks that can run in parallel. For anything smaller,
    use glob_search/grep_search/read_file directly - delegation costs a full
    agent run.

    Results are reports, not ground truth: verify key claims (spot-check the
    cited files or rerun a decisive command) before building on them. Once
    delegated, do not redo the same work yourself.

    Args:
        tasks: Task or list of tasks; each has description (short label),
            prompt (full self-contained instructions), and optional agent_type
            (a named agent definition to use instead of the default)
    """
    return await _task_delegate_impl(tasks)


def _set_task_delegate_schema_limit(limit: int) -> None:
    tasks_schema = task_delegate.params_json_schema["properties"]["tasks"]
    array_schema = next(branch for branch in tasks_schema["anyOf"] if branch.get("type") == "array")
    array_schema["maxItems"] = limit


def refresh_task_delegate_schema_limit(*, strict: bool = True) -> int:
    """Refresh model-visible maxItems without making tool registration fragile."""
    try:
        limit = resolve_task_delegate_max_batch_size()
    except Exception:
        _set_task_delegate_schema_limit(DEFAULT_TASK_DELEGATE_BATCH_SIZE)
        if strict:
            raise
        return DEFAULT_TASK_DELEGATE_BATCH_SIZE
    _set_task_delegate_schema_limit(limit)
    return limit


_set_task_delegate_schema_limit(DEFAULT_TASK_DELEGATE_BATCH_SIZE)
