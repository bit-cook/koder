"""Tool implementations for Koder Agent."""

import platform
from typing import List

from agents import FunctionTool, Tool

from ..agentic.hook_guardrail import hook_pretool_input_guardrail
from ..agentic.plan_guardrail import plan_mode_restriction_guardrail
from ..agentic.skill_guardrail import skill_restriction_guardrail
from .agent import AgentToolModel, agent_tool
from .ask_user import ask_user_question, ask_user_question_tool
from .code_intelligence import CodeIntelligenceModel, code_intelligence, code_intelligence_tool
from .config_tool import config_tool, config_tool_fn
from .cron import (
    cron_create,
    cron_create_tool,
    cron_delete,
    cron_delete_tool,
    cron_list,
    cron_list_tool,
)
from .file import (
    FileEditModel,
    FileReadModel,
    FileWriteModel,
    LSModel,
    append_file,
    edit_file,
    edit_file_by_replacement,
    list_directory,
    read_file,
    write_file,
)
from .goal import create_goal, get_goal, update_goal
from .mcp_resource import list_mcp_resources, read_mcp_resource
from .notebook_edit import notebook_edit
from .plan_mode import (
    enter_plan_mode,
    enter_plan_mode_tool,
    exit_plan_mode,
    exit_plan_mode_tool,
)
from .powershell import PowerShellModel, run_powershell
from .search import GlobModel, GrepModel, glob_search, grep_search
from .send_message import SendMessageModel, send_message
from .shell import (
    BackgroundShellManager,
    GitModel,
    ShellKillModel,
    ShellModel,
    ShellOutputModel,
    git_command,
    run_shell,
    shell_kill,
    shell_output,
)
from .skill import Skill, SkillLoader, SkillModel, get_skill
from .skill_context import (
    SkillRestrictions,
    add_skill_restrictions,
    clear_restrictions,
    get_active_restrictions,
    has_active_restrictions,
)
from .sleep import sleep_tool
from .structured_output import structured_output
from .task import TaskDelegateModel, TaskModel, task_delegate
from .task_lifecycle import (
    TaskCreateModel,
    TaskGetModel,
    TaskListModel,
    TaskUpdateModel,
    task_create,
    task_create_tool,
    task_get,
    task_get_tool,
    task_list,
    task_list_tool,
    task_update,
    task_update_tool,
)
from .task_output import task_output, task_output_tool
from .task_stop import task_stop, task_stop_tool
from .team import TeamCreateModel, TeamDeleteModel, team_create, team_delete
from .todo import TodoModel, TodoWriteModel, todo_read, todo_write
from .tool_search import tool_search, tool_search_tool
from .web import SearchModel, WebFetchModel, web_fetch, web_search
from .worktree import (
    enter_worktree,
    enter_worktree_tool,
    exit_worktree,
    exit_worktree_tool,
)


def get_all_tools() -> List[Tool]:
    """Get all registered tools as a list.

    Each FunctionTool is configured with the skill_restriction_guardrail
    to enforce skill-based tool restrictions when skills are active.
    """
    # Collect all @function_tool decorated functions directly
    tools = [
        read_file,
        write_file,
        append_file,
        edit_file,
        run_shell,
        shell_output,
        shell_kill,
        git_command,
        web_search,
        web_fetch,
        glob_search,
        grep_search,
        code_intelligence_tool,
        list_directory,
        todo_read,
        todo_write,
        task_delegate,
        get_skill,
        agent_tool,
        send_message,
        team_create,
        team_delete,
        # Task lifecycle
        task_create_tool,
        task_update_tool,
        task_get_tool,
        task_list_tool,
        # Plan mode
        enter_plan_mode_tool,
        exit_plan_mode_tool,
        # Worktree
        enter_worktree_tool,
        exit_worktree_tool,
        # ToolSearch
        tool_search_tool,
        # Config
        config_tool_fn,
        # Cron
        cron_create_tool,
        cron_delete_tool,
        cron_list_tool,
        # AskUserQuestion, TaskOutput, TaskStop
        ask_user_question_tool,
        task_output_tool,
        task_stop_tool,
        # Structured output
        structured_output,
        # Notebook editing
        notebook_edit,
        # Sleep (self-throttling)
        sleep_tool,
        # Goals (long-running objectives)
        get_goal,
        create_goal,
        update_goal,
        # MCP resource tools
        list_mcp_resources,
        read_mcp_resource,
    ]

    # PowerShell is Windows-only; never expose it to the model on other platforms.
    if platform.system() == "Windows":
        tools.insert(tools.index(shell_output), run_powershell)

    # Filter to only include properly decorated tools and attach guardrails
    result = []
    for tool in tools:
        if hasattr(tool, "name"):
            # Attach skill restriction guardrail to each FunctionTool
            if isinstance(tool, FunctionTool):
                desired_guardrails = [
                    plan_mode_restriction_guardrail,
                    skill_restriction_guardrail,
                    hook_pretool_input_guardrail,
                ]
                if tool.tool_input_guardrails is None:
                    tool.tool_input_guardrails = list(desired_guardrails)
                else:
                    for guardrail in desired_guardrails:
                        if guardrail not in tool.tool_input_guardrails:
                            tool.tool_input_guardrails.append(guardrail)
            result.append(tool)
    return result


__all__ = [
    "get_all_tools",
    # Manager for cleanup
    "BackgroundShellManager",
    # Models
    "FileEditModel",
    "FileReadModel",
    "FileWriteModel",
    "LSModel",
    "ShellModel",
    "PowerShellModel",
    "ShellOutputModel",
    "ShellKillModel",
    "GitModel",
    "SearchModel",
    "WebFetchModel",
    "GlobModel",
    "GrepModel",
    "CodeIntelligenceModel",
    "Skill",
    "SkillLoader",
    "SkillModel",
    "SkillRestrictions",
    "add_skill_restrictions",
    "clear_restrictions",
    "get_active_restrictions",
    "has_active_restrictions",
    "TodoModel",
    "TodoWriteModel",
    "TaskModel",
    "TaskDelegateModel",
    "AgentToolModel",
    "SendMessageModel",
    "TeamCreateModel",
    "TeamDeleteModel",
    # New orchestration models
    "TaskCreateModel",
    "TaskUpdateModel",
    "TaskGetModel",
    "TaskListModel",
    # Functions
    "read_file",
    "write_file",
    "append_file",
    "edit_file",
    "edit_file_by_replacement",
    "run_shell",
    "run_powershell",
    "shell_output",
    "shell_kill",
    "git_command",
    "web_search",
    "web_fetch",
    "glob_search",
    "grep_search",
    "code_intelligence",
    "code_intelligence_tool",
    "list_directory",
    "todo_read",
    "todo_write",
    "task_delegate",
    "get_skill",
    "get_goal",
    "create_goal",
    "update_goal",
    "agent_tool",
    "send_message",
    "team_create",
    "team_delete",
    # New orchestration tools
    "task_create",
    "task_create_tool",
    "task_update",
    "task_update_tool",
    "task_get",
    "task_get_tool",
    "task_list",
    "task_list_tool",
    "enter_plan_mode",
    "enter_plan_mode_tool",
    "exit_plan_mode",
    "exit_plan_mode_tool",
    "enter_worktree",
    "enter_worktree_tool",
    "exit_worktree",
    "exit_worktree_tool",
    "tool_search",
    "tool_search_tool",
    "config_tool",
    "config_tool_fn",
    "cron_create",
    "cron_create_tool",
    "cron_delete",
    "cron_delete_tool",
    "cron_list",
    "cron_list_tool",
    "ask_user_question",
    "ask_user_question_tool",
    "task_output",
    "task_output_tool",
    "task_stop",
    "task_stop_tool",
    "structured_output",
    "notebook_edit",
    "sleep_tool",
    "list_mcp_resources",
    "read_mcp_resource",
]
