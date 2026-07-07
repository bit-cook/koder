import asyncio
from types import SimpleNamespace

from koder_agent.harness import session_flow


class _FakeStdin:
    def __init__(self, text: str, *, is_tty: bool):
        self._text = text
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty

    def read(self) -> str:
        return self._text


class _FakeSchedulerSession:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def set_title(self, _name: str) -> None:
        return None

    async def get_display_name(self) -> str:
        return self.session_id

    async def get_items(self):
        return []

    async def get_most_recent_session_for_cwd(self, _cwd: str):
        return None


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
    ):
        self.session = _FakeSchedulerSession(session_id)
        self.streaming = streaming
        self.usage_tracker = object()
        self._title_generation_task = None

    async def handle(self, prompt: str, render_output: bool = True, multimodal_input=None) -> str:
        self.prompts.append((prompt, render_output))
        return prompt

    async def cleanup(self) -> None:
        return None


class _FakeInteractivePrompt:
    status_line = None

    def __init__(self, *_args, **_kwargs):
        pass

    async def get_input(self) -> str:
        raise EOFError

    def update_session(self, _session_id: str) -> None:
        return None


class _FakeCommandHandler:
    config_service = None

    def __init__(self, **_kwargs):
        pass

    def get_command_list(self):
        return []

    def is_slash_command(self, _prompt: str) -> bool:
        return False


class _FakeEnhancedSQLiteSession:
    @staticmethod
    async def record_session_cwd(_session_id: str, _cwd: str) -> None:
        return None

    @staticmethod
    async def get_session_agent(_session_id: str):
        return None

    @staticmethod
    async def record_session_agent(_session_id: str, _agent_name: str) -> None:
        return None

    @staticmethod
    async def get_most_recent_session_for_cwd(_cwd: str):
        return None


def _patch_session_flow(monkeypatch, stdin_text: str, *, stdin_is_tty: bool = False) -> None:
    fake_config = SimpleNamespace(cli=SimpleNamespace(stream=False, session=None))
    fake_manager = SimpleNamespace(get_effective_value=lambda _value, _default: None)

    monkeypatch.setattr(session_flow, "get_config", lambda: fake_config)
    monkeypatch.setattr(session_flow, "get_config_manager", lambda: fake_manager)
    monkeypatch.setattr(session_flow, "load_context", _async_value(""))
    monkeypatch.setattr(session_flow, "HarnessInteractiveCommandHandler", _FakeCommandHandler)
    monkeypatch.setattr(session_flow.sys, "stdin", _FakeStdin(stdin_text, is_tty=stdin_is_tty))

    monkeypatch.setattr("koder_agent.utils.setup_openai_client", lambda: None)
    monkeypatch.setattr("koder_agent.utils.default_session_local_ms", lambda: "test-session")
    monkeypatch.setattr(
        "koder_agent.core.session.EnhancedSQLiteSession", _FakeEnhancedSQLiteSession
    )
    monkeypatch.setattr("koder_agent.core.scheduler.AgentScheduler", _FakeScheduler)
    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _FakeInteractivePrompt)

    _FakeScheduler.prompts = []


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def test_run_harness_session_flow_uses_piped_stdin_without_args(monkeypatch):
    _patch_session_flow(monkeypatch, "hello from stdin")

    exit_code = asyncio.run(session_flow.run_harness_session_flow(first_arg=None, argv=[]))

    assert exit_code == 0
    assert _FakeScheduler.prompts == [("hello from stdin", True)]


def test_run_harness_session_flow_combines_prompt_with_piped_stdin(monkeypatch):
    _patch_session_flow(monkeypatch, "hello from stdin")

    exit_code = asyncio.run(
        session_flow.run_harness_session_flow(first_arg=None, argv=["--print", "echo stdin only"])
    )

    assert exit_code == 0
    assert _FakeScheduler.prompts == [
        (
            "echo stdin only\n\nStdin content:\nhello from stdin",
            True,
        )
    ]


def test_run_harness_session_flow_dispatches_session_end_when_cron_stop_fails(monkeypatch):
    _patch_session_flow(monkeypatch, "", stdin_is_tty=True)
    events = []

    class _FailingCronPromptRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self) -> None:
            return None

        async def stop(self) -> None:
            raise RuntimeError("cron stop failed")

    def _fake_dispatch_command_hooks(*, event_name, **_kwargs):
        events.append(event_name)
        return SimpleNamespace(blocked=False, block_reason=None, watch_paths=[])

    from koder_agent.harness.cron import runtime as cron_runtime

    monkeypatch.setattr(cron_runtime, "CronPromptRunner", _FailingCronPromptRunner)
    monkeypatch.setattr(session_flow, "dispatch_command_hooks", _fake_dispatch_command_hooks)

    exit_code = asyncio.run(session_flow.run_harness_session_flow(first_arg=None, argv=[]))

    assert exit_code == 0
    assert "SessionEnd" in events


def test_keyboard_interrupt_exit_skips_auto_dream_consolidation(monkeypatch):
    _patch_session_flow(monkeypatch, "", stdin_is_tty=True)
    calls = []

    class _InterruptPrompt(_FakeInteractivePrompt):
        async def get_input(self) -> str:
            raise KeyboardInterrupt

    class _DreamManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def record_session(self) -> None:
            calls.append("record_session")

        def should_dream(self) -> bool:
            return True

        def save(self) -> None:
            calls.append("save")

    async def _run_auto_dream_from_messages(*_args, **_kwargs):
        calls.append("run_auto_dream")
        return SimpleNamespace(memories_written=0, saved_path=None, errors=[])

    from koder_agent.harness.memory import auto_dream

    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _InterruptPrompt)
    monkeypatch.setattr(auto_dream, "AutoDreamManager", _DreamManager)
    monkeypatch.setattr(auto_dream, "run_auto_dream_from_messages", _run_auto_dream_from_messages)
    monkeypatch.setattr(auto_dream, "default_auto_dream_task_storage", lambda: object())

    exit_code = asyncio.run(session_flow.run_harness_session_flow(first_arg=None, argv=[]))

    assert exit_code == 0
    assert calls == ["record_session", "save"]


def test_eof_exit_allows_auto_dream_consolidation(monkeypatch):
    _patch_session_flow(monkeypatch, "", stdin_is_tty=True)
    calls = []

    class _EofPrompt(_FakeInteractivePrompt):
        async def get_input(self) -> str:
            raise EOFError

    class _DreamManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def record_session(self) -> None:
            calls.append("record_session")

        def should_dream(self) -> bool:
            return True

        def save(self) -> None:
            calls.append("save")

    async def _run_auto_dream_from_messages(*_args, **_kwargs):
        calls.append("run_auto_dream")
        return SimpleNamespace(memories_written=0, saved_path=None, errors=[])

    from koder_agent.harness.memory import auto_dream

    monkeypatch.setattr("koder_agent.core.interactive.InteractivePrompt", _EofPrompt)
    monkeypatch.setattr(auto_dream, "AutoDreamManager", _DreamManager)
    monkeypatch.setattr(auto_dream, "run_auto_dream_from_messages", _run_auto_dream_from_messages)
    monkeypatch.setattr(auto_dream, "default_auto_dream_task_storage", lambda: object())

    exit_code = asyncio.run(session_flow.run_harness_session_flow(first_arg=None, argv=[]))

    assert exit_code == 0
    assert calls == ["record_session", "run_auto_dream"]
