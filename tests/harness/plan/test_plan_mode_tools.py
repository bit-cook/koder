"""Tests for EnterPlanMode and ExitPlanMode tools."""

import json

import pytest

from koder_agent.harness.plan.mode import PlanModeService
from koder_agent.tools.plan_mode import (
    _get_plan_service,
    _set_plan_service,
    enter_plan_mode,
    exit_plan_mode,
)


@pytest.fixture(autouse=True)
def _fresh_plan_service():
    svc = PlanModeService()
    _set_plan_service(svc)
    yield
    _set_plan_service(None)


def test_enter_plan_mode():
    result = json.loads(enter_plan_mode())
    assert "plan mode" in result["message"].lower()


def test_enter_plan_mode_sets_state():
    enter_plan_mode()
    svc = _get_plan_service()
    assert svc.mode == "plan"
    assert svc.pre_plan_mode == "default"


def test_exit_plan_mode():
    enter_plan_mode()
    result = json.loads(exit_plan_mode())
    assert "exit" in result["message"].lower() or "proceed" in result["message"].lower()


def test_exit_plan_mode_restores_state():
    enter_plan_mode()
    exit_plan_mode()
    svc = _get_plan_service()
    assert svc.mode == "default"


def test_exit_plan_mode_without_enter():
    result = json.loads(exit_plan_mode())
    assert "not in plan mode" in result["message"].lower()


def test_plan_mode_read_only_tools():
    svc = _get_plan_service()
    svc.enter_plan_mode()
    allowed = svc.get_allowed_tools_in_plan()
    assert "read_file" in allowed
    assert "glob_search" in allowed
    assert "grep_search" in allowed
    assert "run_shell" not in allowed
    assert "write_file" not in allowed
    assert "edit_file" not in allowed
