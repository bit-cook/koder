import asyncio
import json
from pathlib import Path

from koder_agent.harness.agents.definitions import AgentDefinition
from koder_agent.harness.agents.service import AgentService
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


def _run(command: str, *, handler: HarnessInteractiveCommandHandler) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=None))


def _write_agent_file(path: Path, *, name: str, description: str, body: str, **frontmatter) -> None:
    lines = ["---", f"name: {name}", f"description: {description}"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, dict):
            lines.append(f"{key}: {json.dumps(value)}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_agents_command_can_create_show_and_delete_project_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = HarnessInteractiveCommandHandler()

    created = _run("/agents create project reviewer Reviews code", handler=handler)
    agent_file = tmp_path / ".koder" / "agents" / "reviewer.md"
    assert "agents: created" in created
    assert agent_file.exists()

    shown = _run("/agents show reviewer", handler=handler)
    deleted = _run("/agents delete reviewer", handler=handler)

    assert "agents: reviewer" in shown
    assert str(agent_file) in shown
    assert "agents: deleted" in deleted
    assert not agent_file.exists()


def test_agents_show_command_reports_loaded_agent_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    agent_file = tmp_path / ".koder" / "agents" / "reviewer.md"
    _write_agent_file(
        agent_file,
        name="reviewer",
        description="Reviews code carefully",
        body="You are a reviewer.",
        tools=["Read", "Bash"],
        model="sonnet",
        permissionMode="plan",
        memory="user",
        skills=["lint", "tests"],
        hooks={"Stop": {"type": "command"}},
        color="orange",
        isolation="worktree",
        maxTurns=8,
        background=True,
    )

    handler = HarnessInteractiveCommandHandler()
    shown = _run("/agents show reviewer", handler=handler)

    assert "agents: reviewer" in shown
    assert "source: projectSettings" in shown
    assert f"path: {agent_file}" in shown
    assert "description: Reviews code carefully" in shown
    assert "tools: Read, Bash" in shown
    assert "model: sonnet" in shown
    assert "permission_mode: plan" in shown
    assert "memory: user" in shown
    assert "skills: lint, tests" in shown
    assert "hooks: Stop" in shown
    assert "color: orange" in shown
    assert "isolation: worktree" in shown
    assert "max_turns: 8" in shown
    assert "background: true" in shown
    assert "system_prompt:" in shown


def test_agents_overview_reports_agent_roots_and_failed_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    project_agent = tmp_path / ".koder" / "agents" / "project-reviewer.md"
    user_agent = home / ".koder" / "agents" / "user-helper.md"
    _write_agent_file(
        project_agent,
        name="project-reviewer",
        description="Project reviewer",
        body="You are the project reviewer.",
    )
    _write_agent_file(
        user_agent,
        name="user-helper",
        description="User helper",
        body="You are the user helper.",
    )
    broken_plugin = tmp_path / "plugins" / "broken-plugin"
    broken_plugin.mkdir(parents=True)
    (broken_plugin / "plugin.json").write_text("{broken json", encoding="utf-8")

    handler = HarnessInteractiveCommandHandler(plugin_root=tmp_path / "plugins")
    output = _run("/agents", handler=handler)

    assert f"user_agents_dir: {home / '.koder' / 'agents'}" in output
    assert f"project_agents_dir: {tmp_path / '.koder' / 'agents'}" in output
    assert "failed_files:" in output
    assert str(broken_plugin / "plugin.json") in output


def test_agents_overview_does_not_create_repo_agent_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    handler = HarnessInteractiveCommandHandler()
    output = _run("/agents", handler=handler)

    assert "sample_agent_id" not in output
    assert not (tmp_path / "agent-output").exists()


def test_agents_summary_reports_runtime_agent_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "Reviewed router tests\nDetails hidden from summary"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.service.get_model_client_snapshot",
        lambda model_override: {
            "model_name": "litellm/openai/gpt-4.1",
            "api_key": "hidden-key",
            "base_url": "https://proxy.example/v1",
            "native_openai": False,
            "reasoning_effort": "medium",
            "litellm_kwargs": {
                "model": "openai/gpt-4.1",
                "api_key": "hidden-key",
                "base_url": "https://proxy.example/v1",
            },
        },
    )

    service = AgentService.for_test(tmp_path)
    definition = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General work",
        system_prompt="You are a general-purpose agent.",
        source="built-in",
    )

    async def launch_case():
        record = await service.launch_background(
            agent_definition=definition,
            prompt="Review runtime summary",
            description="Review runtime summary",
            permission_mode="plan",
        )
        await service.wait(record.id)
        return record

    record = asyncio.run(launch_case())
    handler = HarnessInteractiveCommandHandler(agent_service=service)

    listed = _run("/agents summary", handler=handler)
    shown = _run(f"/agents summary {record.id}", handler=handler)

    assert "agents: runtime summaries" in listed
    assert f"- {record.id} general-purpose completed: Completed: Reviewed router tests" in listed
    assert "agents: summary" in shown
    assert f"agent_id: {record.id}" in shown
    assert "summary: Completed: Reviewed router tests" in shown
    assert "permission_mode: plan" in shown
    assert "model_config:" in shown
    assert "  model_override: inherit" in shown
    assert "  model_name: litellm/openai/gpt-4.1" in shown
    assert "  provider: openai" in shown
    assert "  base_url: https://proxy.example/v1" in shown
    assert "  api_key_present: True" in shown
    assert "  reasoning_effort: medium" in shown
    assert "hidden-key" not in shown


def test_agents_summary_reports_missing_runtime_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    handler = HarnessInteractiveCommandHandler(agent_service=AgentService.for_test(tmp_path))

    output = _run("/agents summary agent-missing", handler=handler)

    assert output == "agents: runtime agent not found agent-missing"
