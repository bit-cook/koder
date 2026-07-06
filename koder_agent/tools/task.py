"""Task delegation operations."""

import asyncio
from pathlib import Path
from typing import List, Union

from agents import RunConfig, Runner
from pydantic import BaseModel

from ..core.constants import get_max_turns
from .compat import function_tool


class TaskModel(BaseModel):
    description: str
    prompt: str
    agent_type: str | None = None


class TaskDelegateModel(BaseModel):
    tasks: Union[List[TaskModel], TaskModel]


async def _task_delegate_impl(tasks: Union[List[TaskModel], TaskModel]) -> str:
    """
    Delegate one or more tasks to specialized agents and return the aggregated results.
    If multiple tasks are provided, they will be run in parallel.
    """
    try:
        # Normalize input to always be a list
        if isinstance(tasks, TaskModel):
            task_list = [tasks]
        else:
            task_list = tasks

        async def run_single_task(task: TaskModel) -> tuple[str, str]:
            """Run a single task and return (description, result)."""
            try:
                from ..agentic import create_dev_agent, get_display_hooks
                from ..harness.agents.definitions import (
                    build_agent_system_prompt,
                    filter_tools_for_agent_definition,
                    get_agent_definitions,
                    resolve_agent_mcp_server_configs,
                    resolve_agent_model,
                )
                from . import get_all_tools

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
                        return task.description, f"Error: unknown agent type {task.agent_type}"

                tools = get_all_tools()
                tools = [tool for tool in tools if tool.name != "task_delegate"]
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

                # Run the delegated agent
                result = await Runner.run(
                    delegated_agent,
                    task.prompt,
                    max_turns=get_max_turns(),
                    run_config=RunConfig(),
                    hooks=get_display_hooks(),
                )

                return task.description, result.final_output

            except Exception as e:
                return task.description, f"Error: {e}"

        # Run all tasks in parallel
        results = await asyncio.gather(*[run_single_task(task) for task in task_list])

        # Aggregate results
        if len(results) == 1:
            description, result = results[0]
            return f"Delegated task '{description}' completed successfully:\n\n{result}"
        else:
            # Multiple tasks - format as aggregated report
            aggregated_result = "# Delegated Tasks Results\n\n"
            for i, (description, result) in enumerate(results, 1):
                aggregated_result += f"## Task {i}: {description}\n\n{result}\n\n"

            return aggregated_result

    except Exception as e:
        error_msg = f"Error delegating tasks: {e}"
        return error_msg


@function_tool
async def task_delegate(tasks: Union[List[TaskModel], TaskModel]) -> str:
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
