"""Tests for standalone ``.koder/commands/*.md`` prompt-command discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from koder_agent.harness import paths
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.commands.prompt_commands import (
    PromptCommand,
    discover_prompt_commands,
)


@pytest.fixture
def sandboxed_dirs(tmp_path, monkeypatch):
    """Point user (~/.koder) and project (<cwd>/.koder) at isolated tmp dirs."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.Path.cwd",
        lambda: project,
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.prompt_commands.Path.cwd",
        lambda: project,
    )
    return SimpleNamespace(home=home, project=project)


def _write_command(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


# --- Discovery / rendering unit tests -------------------------------------------------


def test_discovers_project_command(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "---\ndescription: Deploy the app\nargument-hint: [env]\n---\nDeploy to $ARGUMENTS now.",
    )
    commands = discover_prompt_commands(cwd=sandboxed_dirs.project)
    assert "deploy" in commands
    cmd = commands["deploy"]
    assert cmd.description == "Deploy the app"
    assert cmd.argument_hint == "[env]"
    assert cmd.source == "project"


def test_project_overrides_user_on_name_collision(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.home / ".koder" / "commands",
        "deploy",
        "---\ndescription: user deploy\n---\nUSER BODY",
    )
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "---\ndescription: project deploy\n---\nPROJECT BODY",
    )
    commands = discover_prompt_commands(cwd=sandboxed_dirs.project)
    assert commands["deploy"].description == "project deploy"
    assert commands["deploy"].body == "PROJECT BODY"
    assert commands["deploy"].source == "project"


def test_user_command_discovered_when_no_project_collision(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.home / ".koder" / "commands",
        "greet",
        "---\ndescription: user greet\n---\nsay hi",
    )
    commands = discover_prompt_commands(cwd=sandboxed_dirs.project)
    assert "greet" in commands
    assert commands["greet"].source == "user"


def test_render_prompt_substitutes_arguments():
    cmd = PromptCommand(name="deploy", description="", body="Deploy to $ARGUMENTS.")
    assert cmd.render_prompt(["staging"]) == "Deploy to staging."


def test_render_prompt_positional_args():
    # Positional placeholders are 0-indexed, matching the skill loader's
    # ``render_prompt``. When no ``$ARGUMENTS`` token exists, the joined args
    # are also appended (again matching the skill loader).
    cmd = PromptCommand(name="do", description="", body="run $0 then $1")
    assert cmd.render_prompt(["alpha", "beta"]) == ("run alpha then beta\n\nARGUMENTS: alpha beta")


def test_render_prompt_appends_arguments_when_no_placeholder():
    cmd = PromptCommand(name="do", description="", body="Do the thing.")
    rendered = cmd.render_prompt(["foo"])
    assert rendered == "Do the thing.\n\nARGUMENTS: foo"


def test_command_without_frontmatter_uses_filename(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "raw",
        "Just a body, no frontmatter.",
    )
    commands = discover_prompt_commands(cwd=sandboxed_dirs.project)
    assert "raw" in commands
    assert commands["raw"].description == ""
    assert commands["raw"].body == "Just a body, no frontmatter."


def test_no_commands_dir_returns_empty(sandboxed_dirs):
    assert discover_prompt_commands(cwd=sandboxed_dirs.project) == {}


# --- Handler integration tests --------------------------------------------------------


def test_get_command_list_includes_prompt_command(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "---\ndescription: Deploy the app\nargument-hint: [env]\n---\nDeploy to $ARGUMENTS.",
    )
    handler = HarnessInteractiveCommandHandler()
    listing = dict(handler.get_command_list())
    assert "deploy" in listing
    assert "Deploy the app" in listing["deploy"]
    assert "[env]" in listing["deploy"]


def test_slash_dispatch_renders_and_dispatches(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "---\ndescription: Deploy the app\n---\nDeploy to $ARGUMENTS now.",
    )
    handler = HarnessInteractiveCommandHandler()

    dispatched: dict[str, str] = {}

    class _Scheduler:
        async def handle(self, prompt, render_output=None):
            dispatched["prompt"] = prompt
            return "dispatched"

    result = asyncio.run(handler.handle_slash_input("/deploy foo", scheduler=_Scheduler()))
    assert result == "dispatched"
    assert dispatched["prompt"] == "Deploy to foo now."


def test_slash_dispatch_returns_prompt_without_scheduler(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "Ship $ARGUMENTS.",
    )
    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(handler.handle_slash_input("/deploy prod", scheduler=None))
    assert result == "Ship prod."


def test_unknown_command_falls_through(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "body",
    )
    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(handler.handle_slash_input("/definitely-not-a-command", scheduler=None))
    assert result is not None
    assert "Unknown command" in result


def test_project_override_dispatch_prefers_project_body(sandboxed_dirs):
    _write_command(
        sandboxed_dirs.home / ".koder" / "commands",
        "deploy",
        "USER: $ARGUMENTS",
    )
    _write_command(
        sandboxed_dirs.project / ".koder" / "commands",
        "deploy",
        "PROJECT: $ARGUMENTS",
    )
    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(handler.handle_slash_input("/deploy x", scheduler=None))
    assert result == "PROJECT: x"
