"""Plan mode transition service."""

from __future__ import annotations

from dataclasses import dataclass

# Read-only tools allowed in plan mode
_PLAN_MODE_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "glob_search",
        "grep_search",
        "code_intelligence",
        "list_directory",
        "web_search",
        "web_fetch",
        "todo_read",
        "todo_write",
        "task_get",
        "task_list",
        "task_create",
        "task_update",
        "get_skill",
        "enter_worktree",
        "exit_worktree",
        "enter_plan_mode",
        "exit_plan_mode",
        "tool_search",
        "ask_user_question",
    }
)


@dataclass(frozen=True)
class PlanModeResult:
    """Outcome of a plan-mode transition."""

    mode: str
    permission_mode: str


class PlanModeService:
    """Explicit plan-mode enter/exit transitions."""

    def __init__(self, mode: str = "default"):
        self.mode = mode
        self.pre_plan_mode: str | None = None

    @classmethod
    def default(cls) -> PlanModeService:
        return cls()

    def enter_plan_mode(self, permission_mode: str = "plan") -> PlanModeResult:
        self.pre_plan_mode = self.mode
        self.mode = "plan"
        return PlanModeResult(mode=self.mode, permission_mode=permission_mode)

    def exit_plan_mode(self) -> PlanModeResult:
        if self.mode != "plan":
            return PlanModeResult(mode=self.mode, permission_mode=self.mode)
        restored = self.pre_plan_mode or "default"
        self.mode = restored
        self.pre_plan_mode = None
        return PlanModeResult(mode=self.mode, permission_mode=restored)

    def is_plan_mode(self) -> bool:
        return self.mode == "plan"

    def get_allowed_tools_in_plan(self) -> frozenset[str]:
        return _PLAN_MODE_ALLOWED_TOOLS
