"""Regression tests for the skill-restriction ContextVar task-boundary bug.

The openai-agents SDK runs every tool call inside its own asyncio Task, which
executes in a COPY of the parent context. The original ``add_skill_restrictions``
did ``_active_restrictions.set(fresh)`` from inside ``get_skill``'s task, so the
restriction died with that task and never reached sibling / later tool-call tasks
— making the ``allowed_tools`` skill sandbox a complete no-op.

The fix seeds a persistent restrictions container at the run-loop scope (like
``set_tool_permission_context``) so in-place mutation from a child task is visible
to every other tool task that shares the same parent context.
"""

from __future__ import annotations

import asyncio

import pytest

from koder_agent.tools.skill import Skill
from koder_agent.tools.skill_context import (
    add_skill_restrictions,
    begin_skill_restriction_scope,
    clear_restrictions,
    get_active_restrictions,
    reset_skill_restriction_scope,
)


@pytest.fixture(autouse=True)
def _reset():
    clear_restrictions()
    yield
    clear_restrictions()


def _skill(name: str, allowed: list[str]) -> Skill:
    return Skill(name=name, description="d", content="c", allowed_tools=allowed)


def test_restriction_set_in_child_task_visible_to_sibling_task():
    """A skill loaded inside one tool-call task must restrict a later task."""

    async def scenario():
        # The scheduler seeds the scope before Runner.run.
        token = begin_skill_restriction_scope()
        try:
            # get_skill runs in its OWN task (SDK behavior).
            async def load_skill_task():
                add_skill_restrictions(_skill("read-only", ["read_file"]))

            await asyncio.create_task(load_skill_task())

            # A later tool call runs in a DIFFERENT task; it must observe the
            # restriction the earlier task registered.
            async def later_tool_task():
                restrictions = get_active_restrictions()
                assert restrictions is not None, "restriction did not cross task boundary"
                assert restrictions.is_tool_allowed("read_file") is True
                assert restrictions.is_tool_allowed("run_shell") is False
                return True

            return await asyncio.create_task(later_tool_task())
        finally:
            reset_skill_restriction_scope(token)

    assert asyncio.run(scenario()) is True


def test_scope_reset_clears_restrictions_for_next_run():
    """After a scope is reset, a fresh scope starts with no restrictions."""

    async def scenario():
        token = begin_skill_restriction_scope()
        add_skill_restrictions(_skill("s", ["read_file"]))
        assert get_active_restrictions() is not None
        reset_skill_restriction_scope(token)

        # New run/scope: nothing carried over.
        token2 = begin_skill_restriction_scope()
        try:
            assert get_active_restrictions() is None or not get_active_restrictions().allowed_tools
        finally:
            reset_skill_restriction_scope(token2)

    asyncio.run(scenario())


def test_union_across_two_skills_in_separate_tasks():
    """Two restricted skills loaded in separate tasks accumulate (union)."""

    async def scenario():
        token = begin_skill_restriction_scope()
        try:
            await asyncio.create_task(_add("a", ["read_file"]))
            await asyncio.create_task(_add("b", ["glob_search"]))
            r = get_active_restrictions()
            assert r is not None
            assert r.is_tool_allowed("read_file")
            assert r.is_tool_allowed("glob_search")
            assert not r.is_tool_allowed("run_shell")
        finally:
            reset_skill_restriction_scope(token)

    async def _add(name, allowed):
        add_skill_restrictions(_skill(name, allowed))

    asyncio.run(scenario())


def test_nested_scheduler_scope_inherits_manual_policy_without_leaking_additions():
    """A scheduler scope inherits a slash skill and restores its parent snapshot."""

    async def scenario():
        manual_token = begin_skill_restriction_scope(_skill("manual", ["read_file"]))
        try:
            scheduler_token = begin_skill_restriction_scope()
            try:
                inherited = get_active_restrictions()
                assert inherited is not None
                assert inherited.loaded_skills == ["manual"]
                assert inherited.is_tool_allowed("read_file")
                assert not inherited.is_tool_allowed("write_file")

                await asyncio.create_task(_add("nested", ["glob_search"]))
                nested = get_active_restrictions()
                assert nested is not None
                assert nested.loaded_skills == ["manual", "nested"]
                assert nested.is_tool_allowed("glob_search")
            finally:
                reset_skill_restriction_scope(scheduler_token)

            restored = get_active_restrictions()
            assert restored is not None
            assert restored.loaded_skills == ["manual"]
            assert restored.allowed_tools == {"read_file"}
        finally:
            reset_skill_restriction_scope(manual_token)

        assert get_active_restrictions() is None

    async def _add(name, allowed):
        add_skill_restrictions(_skill(name, allowed))

    asyncio.run(scenario())
