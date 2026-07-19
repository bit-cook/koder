import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from koder_agent.harness import session_flow
from koder_agent.tools.todo import TodoRuntimeIdentity, TodoStore


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
    instances = []

    def __init__(
        self,
        session_id: str,
        streaming: bool,
        agent_definition=None,
        instructions_override=None,
        instructions_append=None,
        permission_service=None,
        approver=None,
        todo_store=None,
    ):
        self.session = _FakeSchedulerSession(session_id)
        self.streaming = streaming
        self.agent_definition = agent_definition
        agent_id = agent_definition.agent_type if agent_definition is not None else "main"
        self.todo_store = todo_store or TodoStore(
            TodoRuntimeIdentity(session_id, agent_id, f"test:{session_id}:{agent_id}")
        )
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
        self.cleanup_calls = 0
        _FakeScheduler.last_agent = agent_definition
        _FakeScheduler.instances.append(self)

    async def handle(self, prompt: str, render_output: bool = True, multimodal_input=None) -> str:
        return prompt

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


def _patch_runtime(monkeypatch):
    fake_config = SimpleNamespace(cli=SimpleNamespace(stream=False, session=None))
    fake_manager = SimpleNamespace(get_effective_value=lambda _value, _default: None)

    monkeypatch.setattr(session_flow, "get_config", lambda: fake_config)
    monkeypatch.setattr(session_flow, "get_config_manager", lambda: fake_manager)
    monkeypatch.setattr(session_flow, "load_context", _async_value(""))
    monkeypatch.setattr("koder_agent.utils.setup_openai_client", lambda: None)
    monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _FakeScheduler)
    monkeypatch.setattr("koder_agent.harness.session_flow._read_piped_stdin", lambda: None)
    _FakeScheduler.instances = []


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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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


def test_startup_resume_restores_target_project_before_agent_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _patch_runtime(monkeypatch)
    origin = tmp_path / "origin"
    other = tmp_path / "other"
    for project, prompt in ((origin, "Origin definition."), (other, "Other definition.")):
        (project / ".koder" / "agents").mkdir(parents=True)
        (project / ".koder" / "agents" / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: Reviews code\n---\n" + prompt + "\n",
            encoding="utf-8",
        )

    monkeypatch.chdir(origin)
    assert (
        asyncio.run(
            session_flow.run_harness_session_flow(
                first_arg=None,
                argv=["--session", "startup-target", "--agent", "reviewer", "-p", "/session"],
            )
        )
        == 0
    )

    monkeypatch.chdir(other)
    assert (
        asyncio.run(
            session_flow.run_harness_session_flow(
                first_arg=None,
                argv=["--resume", "startup-target", "-p", "/session"],
            )
        )
        == 0
    )

    assert session_flow.Path.cwd() == origin
    assert _FakeScheduler.last_agent is not None
    assert _FakeScheduler.last_agent.system_prompt == "Origin definition."


def test_failed_startup_resume_does_not_rewrite_saved_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _patch_runtime(monkeypatch)
    origin = tmp_path / "origin"
    other = tmp_path / "other"
    (origin / ".koder" / "agents").mkdir(parents=True)
    (other / ".koder" / "agents").mkdir(parents=True)
    origin_definition = origin / ".koder" / "agents" / "reviewer.md"
    origin_definition.write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\nOrigin definition.\n",
        encoding="utf-8",
    )
    (other / ".koder" / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\nOther definition.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(origin)
    assert (
        asyncio.run(
            session_flow.run_harness_session_flow(
                first_arg=None,
                argv=["--session", "startup-failure", "--agent", "reviewer", "-p", "/session"],
            )
        )
        == 0
    )
    origin_definition.unlink()

    monkeypatch.chdir(other)
    assert (
        asyncio.run(
            session_flow.run_harness_session_flow(
                first_arg=None,
                argv=["--resume", "startup-failure", "-p", "/session"],
            )
        )
        == 1
    )

    from koder_agent.core.session import EnhancedSQLiteSession

    saved_cwd = asyncio.run(EnhancedSQLiteSession("startup-failure").get_cwd())
    assert saved_cwd == str(origin)
    assert session_flow.Path.cwd() == other


def test_session_switch_restores_agent_and_reuses_store_by_session_and_agent(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    recorded_agents = {"custom-session": "reviewer", "main-session": None}

    class _MetadataSession:
        def __init__(self, session_id: str):
            self.session_id = session_id

        async def get_cwd(self):
            return None

        @staticmethod
        async def record_session_cwd(_session_id: str, _cwd: str) -> None:
            return None

        @staticmethod
        async def get_most_recent_session_for_cwd(_cwd: str):
            return None

        @staticmethod
        async def get_session_agent(session_id: str):
            return recorded_agents.get(session_id)

        @staticmethod
        async def record_session_agent(session_id: str, agent_name: str) -> None:
            recorded_agents[session_id] = agent_name

    class _Prompt:
        status_line = None
        inputs = iter(
            [
                "/switch custom-session",
                "/switch main-session",
                "/switch custom-session",
                "exit",
            ]
        )

        def __init__(self, *_args, **_kwargs):
            pass

        async def get_input(self):
            return next(self.inputs)

        def update_session(self, _session_id):
            return None

        def reset_history(self):
            return None

    class _CommandHandler:
        config_service = None

        def __init__(self, **_kwargs):
            pass

        def get_command_list(self):
            return []

        def is_slash_command(self, value):
            return value.startswith("/")

        async def handle_slash_input(self, value, _scheduler):
            return f"session_switch:{value.split(maxsplit=1)[1]}"

    monkeypatch.setattr(
        "koder_agent.core.session.EnhancedSQLiteSession",
        _MetadataSession,
    )
    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _Prompt)
    monkeypatch.setattr(session_flow, "HarnessInteractiveCommandHandler", _CommandHandler)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=[
                "--session",
                "main-session",
                "--agents",
                '{"reviewer":{"description":"Reviews code","prompt":"Review code."}}',
            ],
        )
    )

    assert exit_code == 0
    assert [instance.session.session_id for instance in _FakeScheduler.instances] == [
        "main-session",
        "custom-session",
        "main-session",
        "custom-session",
    ]
    assert [
        instance.agent_definition.agent_type if instance.agent_definition else "main"
        for instance in _FakeScheduler.instances
    ] == ["main", "reviewer", "main", "reviewer"]
    assert _FakeScheduler.instances[0].todo_store is _FakeScheduler.instances[2].todo_store
    assert _FakeScheduler.instances[1].todo_store is _FakeScheduler.instances[3].todo_store
    assert _FakeScheduler.instances[0].todo_store is not _FakeScheduler.instances[1].todo_store


def test_session_switch_loads_target_project_agent_before_replacing_scheduler(
    monkeypatch, tmp_path
):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    current_project = tmp_path / "current"
    target_project = tmp_path / "target"
    current_project.mkdir()
    (target_project / ".koder" / "agents").mkdir(parents=True)
    (target_project / ".koder" / "agents" / "target-reviewer.md").write_text(
        "---\nname: target-reviewer\ndescription: Target-only reviewer\n---\n"
        "Review the target project.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(current_project)

    recorded_agents = {"main-session": None, "target-session": "target-reviewer"}
    recorded_cwds = {
        "main-session": str(current_project),
        "target-session": str(target_project),
    }

    class _MetadataSession:
        def __init__(self, session_id: str):
            self.session_id = session_id

        async def get_cwd(self):
            return recorded_cwds.get(self.session_id)

        @staticmethod
        async def record_session_cwd(session_id: str, cwd: str) -> None:
            recorded_cwds[session_id] = cwd

        @staticmethod
        async def get_most_recent_session_for_cwd(_cwd: str):
            return None

        @staticmethod
        async def get_session_agent(session_id: str):
            return recorded_agents.get(session_id)

        @staticmethod
        async def record_session_agent(session_id: str, agent_name: str) -> None:
            recorded_agents[session_id] = agent_name

    class _Prompt:
        status_line = None
        inputs = iter(["/switch target-session", "exit"])

        def __init__(self, *_args, **_kwargs):
            pass

        async def get_input(self):
            return next(self.inputs)

        def update_session(self, _session_id):
            return None

        def reset_history(self):
            return None

    class _CommandHandler:
        config_service = None

        def __init__(self, **_kwargs):
            pass

        def get_command_list(self):
            return []

        def is_slash_command(self, value):
            return value.startswith("/")

        async def handle_slash_input(self, value, _scheduler):
            return f"session_switch:{value.split(maxsplit=1)[1]}"

    monkeypatch.setattr("koder_agent.core.session.EnhancedSQLiteSession", _MetadataSession)
    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _Prompt)
    monkeypatch.setattr(session_flow, "HarnessInteractiveCommandHandler", _CommandHandler)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["--session", "main-session"],
        )
    )

    assert exit_code == 0
    assert [instance.session.session_id for instance in _FakeScheduler.instances] == [
        "main-session",
        "target-session",
    ]
    target_scheduler = _FakeScheduler.instances[1]
    assert target_scheduler.agent_definition.agent_type == "target-reviewer"
    assert target_scheduler.todo_store.identity.session_id == "target-session"
    assert target_scheduler.todo_store.identity.agent_id == "target-reviewer"
    assert session_flow.Path.cwd() == target_project


def test_session_switch_missing_target_agent_keeps_original_scheduler(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    current_project = tmp_path / "current"
    target_project = tmp_path / "target"
    current_project.mkdir()
    target_project.mkdir()
    monkeypatch.chdir(current_project)

    recorded_agents = {"main-session": None, "target-session": "missing-target-agent"}
    recorded_cwds = {
        "main-session": str(current_project),
        "target-session": str(target_project),
    }
    rendered = []

    class _MetadataSession:
        def __init__(self, session_id: str):
            self.session_id = session_id

        async def get_cwd(self):
            return recorded_cwds.get(self.session_id)

        @staticmethod
        async def record_session_cwd(session_id: str, cwd: str) -> None:
            recorded_cwds[session_id] = cwd

        @staticmethod
        async def get_most_recent_session_for_cwd(_cwd: str):
            return None

        @staticmethod
        async def get_session_agent(session_id: str):
            return recorded_agents.get(session_id)

        @staticmethod
        async def record_session_agent(session_id: str, agent_name: str) -> None:
            recorded_agents[session_id] = agent_name

    class _Prompt:
        status_line = None
        inputs = iter(["/switch target-session", "exit"])

        def __init__(self, *_args, **_kwargs):
            pass

        async def get_input(self):
            return next(self.inputs)

        def update_session(self, _session_id):
            return None

        def reset_history(self):
            return None

    class _CommandHandler:
        config_service = None

        def __init__(self, **_kwargs):
            pass

        def get_command_list(self):
            return []

        def is_slash_command(self, value):
            return value.startswith("/")

        async def handle_slash_input(self, value, _scheduler):
            return f"session_switch:{value.split(maxsplit=1)[1]}"

    monkeypatch.setattr("koder_agent.core.session.EnhancedSQLiteSession", _MetadataSession)
    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _Prompt)
    monkeypatch.setattr(session_flow, "HarnessInteractiveCommandHandler", _CommandHandler)
    monkeypatch.setattr(
        session_flow,
        "print_reflowable",
        lambda _console, value: rendered.append(getattr(value, "renderable", value)),
    )

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(
            first_arg=None,
            argv=["--session", "main-session"],
        )
    )

    assert exit_code == 0
    assert len(_FakeScheduler.instances) == 1
    original_scheduler = _FakeScheduler.instances[0]
    assert original_scheduler.session.session_id == "main-session"
    assert original_scheduler.cleanup_calls == 1
    assert session_flow.Path.cwd() == current_project
    assert any("recorded agent is unavailable" in str(value) for value in rendered)


def test_switch_resolves_target_project_before_constructor_and_commit(monkeypatch, tmp_path):
    async def scenario():
        from koder_agent.core.scheduler import AgentScheduler
        from koder_agent.core.session import EnhancedSQLiteSession
        from koder_agent.harness.agents.definitions import get_agent_definitions

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        current_project = tmp_path / "current"
        target_project = tmp_path / "target"
        for project, prompt in (
            (current_project, "Current project definition."),
            (target_project, "Target project definition."),
        ):
            agents_dir = project / ".koder" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "reviewer.md").write_text(
                "---\nname: reviewer\ndescription: Reviews code\n---\n" + prompt + "\n",
                encoding="utf-8",
            )

        monkeypatch.chdir(current_project)
        current_definitions = get_agent_definitions(cwd=current_project)
        current_agent = next(
            agent for agent in current_definitions.active_agents if agent.agent_type == "reviewer"
        )
        await EnhancedSQLiteSession.record_session_cwd(
            "target-session",
            str(target_project),
        )
        await EnhancedSQLiteSession.record_session_agent("target-session", "reviewer")

        monkeypatch.setattr("koder_agent.core.scheduler.get_all_tools", lambda: [])
        builder = session_flow._SchedulerBuilder(
            scheduler_type=AgentScheduler,
            streaming=False,
            agent_definition=current_agent,
            instructions_override=None,
            instructions_append=None,
            permission_service=None,
            approver=None,
            agent_definitions=current_definitions,
        )
        state = session_flow._SchedulerState.create(builder, "current-session")
        old = state.scheduler
        args = SimpleNamespace(session="current-session", agent="reviewer")

        replacement = await session_flow._switch_active_session(
            state,
            args,
            "target-session",
        )

        assert state.scheduler is replacement
        assert Path.cwd() == target_project.resolve()
        assert replacement.project_root == target_project.resolve()
        assert replacement.hooks.cwd == target_project.resolve()
        assert replacement._session_memory.project_dir == target_project.resolve()
        assert replacement.agent_definition.system_prompt == "Target project definition."
        assert replacement.agent_definition is not current_agent
        assert replacement.todo_store.identity.session_id == "target-session"
        assert replacement.todo_store.identity.agent_id == "reviewer"
        assert args.session == "target-session"
        assert args.agent == "reviewer"

        await state.cleanup()
        assert old._cleanup_task is not None

    asyncio.run(scenario())


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
