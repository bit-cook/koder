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
    prompts: list[tuple[str, bool]] = []

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
        self.todo_store = todo_store or SimpleNamespace(session_id=session_id)
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

    async def handle(self, prompt: str, render_output: bool = True, multimodal_input=None) -> str:
        self.prompts.append((prompt, render_output))
        return prompt

    async def cleanup(self) -> None:
        return None


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def _patch_runtime(monkeypatch):
    fake_config = SimpleNamespace(cli=SimpleNamespace(stream=False, session=None))
    fake_manager = SimpleNamespace(get_effective_value=lambda _value, _default: None)
    monkeypatch.setattr(session_flow, "get_config", lambda: fake_config)
    monkeypatch.setattr(session_flow, "get_config_manager", lambda: fake_manager)
    monkeypatch.setattr(session_flow, "load_context", _async_value(""))
    monkeypatch.setattr("koder_agent.utils.setup_openai_client", lambda: None)
    monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _FakeScheduler)
    monkeypatch.setattr("koder_agent.harness.session_flow._read_piped_stdin", lambda: None)

    class _FakeEnhancedSQLiteSession:
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
        async def get_session_agent(_session_id: str):
            return None

        @staticmethod
        async def record_session_agent(_session_id: str, _agent_name: str) -> None:
            return None

    monkeypatch.setattr(
        "koder_agent.core.session.EnhancedSQLiteSession",
        _FakeEnhancedSQLiteSession,
    )
    _FakeScheduler.prompts = []


def test_session_start_hook_runs_before_prompt_execution(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    marker = tmp_path / "session-start.json"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(first_arg=None, argv=["--print", "hello"])
    )

    assert exit_code == 0
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "SessionStart"
    assert _FakeScheduler.prompts == [("hello", True)]


def test_user_prompt_submit_hook_can_block_prompt_execution(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "import sys; print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"blocked prompt\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(first_arg=None, argv=["--print", "hello"])
    )

    assert exit_code == 1
    assert _FakeScheduler.prompts == []


def test_session_end_hook_runs_on_exit(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    marker = tmp_path / "session-end.json"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {
                            "matcher": "other",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(first_arg=None, argv=["--print", "hello"])
    )

    assert exit_code == 0
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "SessionEnd"


def test_instructions_loaded_hook_runs_when_agents_md_context_is_loaded(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    marker = tmp_path / "instructions-loaded.json"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "InstructionsLoaded": [
                        {
                            "matcher": "session_start",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (project_root / "AGENTS.md").write_text("Project guidance", encoding="utf-8")
    monkeypatch.chdir(project_root)

    context = asyncio.run(session_flow.load_context())

    assert "Project guidance" in context
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "InstructionsLoaded"


def test_instructions_loaded_hook_can_block_context_injection(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "InstructionsLoaded": [
                        {
                            "matcher": "session_start",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"skip instructions\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (project_root / "AGENTS.md").write_text("Blocked guidance", encoding="utf-8")
    monkeypatch.chdir(project_root)

    context = asyncio.run(session_flow.load_context())

    assert "Blocked guidance" not in context


def test_stop_failure_hook_runs_when_scheduler_raises(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    project_root = tmp_path / "project"
    marker = tmp_path / "stop-failure.json"
    (project_root / ".koder").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "StopFailure": [
                        {
                            "matcher": "unknown",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    class _BrokenScheduler(_FakeScheduler):
        async def handle(
            self, prompt: str, render_output: bool = True, multimodal_input=None
        ) -> str:
            raise RuntimeError("scheduler exploded")

    monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _BrokenScheduler)

    try:
        asyncio.run(
            session_flow.run_harness_session_flow(first_arg=None, argv=["--print", "hello"])
        )
    except RuntimeError:
        pass

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "StopFailure"
