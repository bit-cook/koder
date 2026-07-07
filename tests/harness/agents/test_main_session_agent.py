import asyncio
import json
from types import SimpleNamespace

from koder_agent.harness import session_flow


class _FakeSchedulerSession:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def set_title(self, _name: str) -> None:
        return None

    async def get_display_name(self) -> str:
        return self.session_id

    async def get_items(self):
        return []


class _FakeScheduler:
    last_agent = None

    def __init__(
        self,
        session_id: str,
        streaming: bool,
        agent_definition=None,
        instructions_override=None,
        instructions_append=None,
        permission_service=None,
        approver=None,
    ):
        self.session = _FakeSchedulerSession(session_id)
        self.streaming = streaming
        self.agent_definition = agent_definition
        self.usage_tracker = SimpleNamespace(
            model="gpt-5.4",
            session_usage=SimpleNamespace(
                request_count=0,
                input_tokens=0,
                output_tokens=0,
                total_cost=0.0,
                current_context_tokens=0,
            ),
        )
        self._title_generation_task = None
        self._mcp_servers = []
        _FakeScheduler.last_agent = agent_definition

    async def handle(self, prompt: str, render_output: bool = True, multimodal_input=None) -> str:
        return prompt

    async def cleanup(self) -> None:
        return None


def _patch_runtime(monkeypatch):
    fake_config = SimpleNamespace(cli=SimpleNamespace(stream=False, session=None))
    fake_manager = SimpleNamespace(get_effective_value=lambda _value, _default: None)

    monkeypatch.setattr(session_flow, "get_config", lambda: fake_config)
    monkeypatch.setattr(session_flow, "get_config_manager", lambda: fake_manager)
    monkeypatch.setattr(session_flow, "load_context", _async_value(""))
    monkeypatch.setattr("koder_agent.utils.setup_openai_client", lambda: None)
    monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _FakeScheduler)
    monkeypatch.setattr("koder_agent.harness.session_flow._read_piped_stdin", lambda: None)


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def test_main_session_agent_can_be_selected_via_cli_flag(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=[
                "--agents",
                '{"reviewer":{"description":"Reviews code","prompt":"You are a reviewer."}}',
                "--agent",
                "reviewer",
                "-p",
                "/session",
            ],
        )
    )

    assert exit_code == 0
    assert _FakeScheduler.last_agent is not None
    assert _FakeScheduler.last_agent.agent_type == "reviewer"


def test_main_session_agent_can_be_loaded_from_project_settings(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".koder" / "agents").mkdir(parents=True)
    (project_root / ".koder" / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\nYou are a reviewer.\n",
        encoding="utf-8",
    )
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps({"agent": "reviewer"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["-p", "/session"],
        )
    )

    assert exit_code == 0
    assert _FakeScheduler.last_agent is not None
    assert _FakeScheduler.last_agent.agent_type == "reviewer"


def test_main_session_agent_persists_on_resume(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".koder" / "agents").mkdir(parents=True)
    (project_root / ".koder" / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\nYou are a reviewer.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    first = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["--session", "saved-agent-session", "--agent", "reviewer", "-p", "/session"],
        )
    )
    assert first == 0

    second = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["--resume", "saved-agent-session", "-p", "/session"],
        )
    )
    assert second == 0
    assert _FakeScheduler.last_agent is not None
    assert _FakeScheduler.last_agent.agent_type == "reviewer"


def test_main_session_agent_initial_prompt_is_prepended(monkeypatch, tmp_path):
    captured_prompts: list[str] = []

    class _PromptScheduler(_FakeScheduler):
        async def handle(
            self, prompt: str, render_output: bool = True, multimodal_input=None
        ) -> str:
            captured_prompts.append(prompt)
            return "ok"

    def _patch_with_scheduler():
        fake_config = SimpleNamespace(cli=SimpleNamespace(stream=False, session=None))
        fake_manager = SimpleNamespace(get_effective_value=lambda _value, _default: None)
        monkeypatch.setattr(session_flow, "get_config", lambda: fake_config)
        monkeypatch.setattr(session_flow, "get_config_manager", lambda: fake_manager)
        monkeypatch.setattr(session_flow, "load_context", _async_value(""))
        monkeypatch.setattr("koder_agent.utils.setup_openai_client", lambda: None)
        monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _PromptScheduler)
        monkeypatch.setattr("koder_agent.harness.session_flow._read_piped_stdin", lambda: None)

    _patch_with_scheduler()
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".koder" / "agents").mkdir(parents=True)
    (project_root / ".koder" / "agents" / "bootstrapper.md").write_text(
        "---\nname: bootstrapper\ndescription: Bootstraps work\ninitialPrompt: Review the repo first\n---\nYou are a bootstrapper.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["--agent", "bootstrapper", "-p", "Implement auth"],
        )
    )

    assert exit_code == 0
    assert captured_prompts
    assert captured_prompts[0] == "Review the repo first"
    assert captured_prompts[-1] == "Implement auth"


def test_agent_mention_runs_explicit_subagent(monkeypatch, tmp_path, capsys):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    async def fake_run_sync(self, *, agent_definition, prompt, seed_items=None, cwd=None):
        return f"agent={agent_definition.agent_type}; prompt={prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service.AgentService.run_sync", fake_run_sync)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=[
                "--agents",
                '{"reviewer":{"description":"Reviews code","prompt":"You are a reviewer."}}',
                "-p",
                "@agent-reviewer Inspect auth",
            ],
        )
    )

    assert exit_code == 0
    assert "agent=reviewer; prompt=Inspect auth" in capsys.readouterr().out
