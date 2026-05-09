"""EnterPlanMode and ExitPlanMode tools."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar

from koder_agent.harness.plan.mode import PlanModeService

from .compat import function_tool

# --- Service singleton ---

_service: PlanModeService | None = None
_service_override: ContextVar[PlanModeService | None] = ContextVar(
    "plan_mode_service_override",
    default=None,
)


def _get_plan_service() -> PlanModeService:
    override = _service_override.get()
    if override is not None:
        return override
    global _service
    if _service is None:
        _service = PlanModeService()
    return _service


def _set_plan_service(service: PlanModeService | None) -> None:
    global _service
    _service = service
    _service_override.set(None)


@contextmanager
def plan_service_scope(service: PlanModeService):
    """Temporarily override the active plan-mode service for the current task."""

    token = _service_override.set(service)
    try:
        yield service
    finally:
        _service_override.reset(token)


# --- Plain implementations ---


def enter_plan_mode() -> str:
    """Enter plan mode for exploring and designing before implementation."""
    svc = _get_plan_service()
    result = svc.enter_plan_mode()
    return json.dumps(
        {
            "message": (
                "Entered plan mode. You should now focus on exploring the codebase, "
                "understanding the problem, and creating a plan. Write operations are "
                "restricted. When your plan is ready, use exit_plan_mode to proceed."
            ),
            "mode": result.mode,
        }
    )


def exit_plan_mode() -> str:
    """Exit plan mode and return to normal execution mode."""
    svc = _get_plan_service()
    if not svc.is_plan_mode():
        return json.dumps(
            {
                "message": "You are not in plan mode. No action taken.",
            }
        )
    result = svc.exit_plan_mode()
    return json.dumps(
        {
            "message": "Exited plan mode. You can now proceed with implementation.",
            "mode": result.mode,
        }
    )


# --- @function_tool wrappers ---

enter_plan_mode_tool = function_tool(enter_plan_mode)
exit_plan_mode_tool = function_tool(exit_plan_mode)
