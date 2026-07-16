"""Tests for AutoDream background memory consolidation."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.auto_dream import (
    AutoDreamManager,
    DreamConfig,
    DreamState,
    list_auto_dream_tasks,
    reconcile_stale_auto_dream_tasks,
    run_auto_dream_from_messages,
)
from koder_agent.harness.memory.candidates import CandidateStore
from koder_agent.harness.memory.extraction import ExtractionResult
from koder_agent.harness.memory.memory_files import parse_memory_file
from koder_agent.harness.memory.retrieval import retrieve_relevant_memories
from koder_agent.harness.tasks.storage import TaskStorage


def _origin_kwargs(tmp_path: Path, *, session_id: str = "auto-dream-test-session") -> dict:
    project_root = tmp_path / "origin-project"
    project_root.mkdir(parents=True, exist_ok=True)
    return {
        "origin_project_root": project_root,
        "origin_session_id": session_id,
    }


class TestDreamConfig:
    """Test DreamConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DreamConfig()
        assert config.enabled is True
        assert config.cooldown_minutes == 60
        assert config.min_sessions == 3
        assert config.write_mode == "review"

    def test_custom_values(self):
        """Test custom configuration values."""
        config = DreamConfig(
            enabled=False,
            cooldown_minutes=30,
            min_sessions=5,
            write_mode="off",
        )
        assert config.enabled is False
        assert config.cooldown_minutes == 30
        assert config.min_sessions == 5
        assert config.write_mode == "off"

    def test_invalid_write_mode_is_rejected(self):
        with pytest.raises(ValueError, match="write_mode"):
            DreamConfig(write_mode="unsafe")


class TestDreamState:
    """Test DreamState dataclass."""

    def test_initial_state(self):
        """Test initial state values."""
        state = DreamState(
            last_dream_time=None,
            session_count=0,
            dream_count=0,
        )
        assert state.last_dream_time is None
        assert state.session_count == 0
        assert state.dream_count == 0

    def test_with_values(self):
        """Test state with specific values."""
        now = time.time()
        state = DreamState(
            last_dream_time=now,
            session_count=5,
            dream_count=2,
        )
        assert state.last_dream_time == now
        assert state.session_count == 5
        assert state.dream_count == 2


class TestAutoDreamManager:
    """Test AutoDreamManager class."""

    def test_init_default(self):
        """Test initialization with defaults."""
        manager = AutoDreamManager()
        assert manager.config.enabled is True
        assert manager.state.session_count == 0
        assert manager.state.last_dream_time is None

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config = DreamConfig(enabled=False, cooldown_minutes=30, min_sessions=5)
        manager = AutoDreamManager(config=config)
        assert manager.config.enabled is False
        assert manager.config.cooldown_minutes == 30
        assert manager.config.min_sessions == 5

    def test_should_dream_disabled(self):
        """Test should_dream returns False when disabled."""
        config = DreamConfig(enabled=False)
        manager = AutoDreamManager(config=config)
        manager.record_session()
        manager.record_session()
        manager.record_session()
        assert manager.should_dream() is False

    def test_should_dream_no_sessions(self):
        """Test should_dream returns False when no sessions."""
        manager = AutoDreamManager()
        assert manager.should_dream() is False

    def test_should_dream_not_enough_sessions(self):
        """Test should_dream returns False when not enough sessions."""
        manager = AutoDreamManager()
        manager.record_session()
        manager.record_session()
        # Need 3 sessions by default
        assert manager.should_dream() is False

    def test_should_dream_during_cooldown(self):
        """Test should_dream returns False during cooldown period."""
        config = DreamConfig(cooldown_minutes=1, min_sessions=2)
        manager = AutoDreamManager(config=config)

        # Record enough sessions
        manager.record_session()
        manager.record_session()

        # Simulate a recent dream
        manager.state.last_dream_time = time.time()

        assert manager.should_dream() is False

    def test_should_dream_after_cooldown(self):
        """Test should_dream returns True after cooldown and enough sessions."""
        config = DreamConfig(cooldown_minutes=1, min_sessions=2)
        manager = AutoDreamManager(config=config)

        # Record enough sessions
        manager.record_session()
        manager.record_session()

        # Simulate a dream that happened more than cooldown ago
        manager.state.last_dream_time = time.time() - (61 * 60)  # 61 minutes ago

        assert manager.should_dream() is True

    def test_should_dream_first_time_with_enough_sessions(self):
        """Test should_dream returns True for first dream with enough sessions."""
        config = DreamConfig(min_sessions=2)
        manager = AutoDreamManager(config=config)

        manager.record_session()
        manager.record_session()

        # No previous dream, so last_dream_time is None
        assert manager.state.last_dream_time is None
        assert manager.should_dream() is True

    def test_record_session_increments_count(self):
        """Test record_session increments session count."""
        manager = AutoDreamManager()
        assert manager.state.session_count == 0

        manager.record_session()
        assert manager.state.session_count == 1

        manager.record_session()
        assert manager.state.session_count == 2

    def test_record_dream_updates_state(self):
        """Test record_dream updates state correctly."""
        manager = AutoDreamManager()
        manager.record_session()
        manager.record_session()
        manager.record_session()

        assert manager.state.session_count == 3
        assert manager.state.dream_count == 0
        assert manager.state.last_dream_time is None

        before = time.time()
        manager.record_dream()
        after = time.time()

        assert manager.state.session_count == 0
        assert manager.state.dream_count == 1
        assert manager.state.last_dream_time is not None
        assert before <= manager.state.last_dream_time <= after

    def test_record_dream_increments_count(self):
        """Test record_dream increments dream count."""
        manager = AutoDreamManager()

        manager.record_dream()
        assert manager.state.dream_count == 1

        manager.record_dream()
        assert manager.state.dream_count == 2

    def test_get_dream_prompt_not_empty(self):
        """Test get_dream_prompt returns non-empty string."""
        manager = AutoDreamManager()
        prompt = manager.get_dream_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_get_dream_prompt_contains_key_terms(self):
        """Test get_dream_prompt contains key consolidation terms."""
        manager = AutoDreamManager()
        prompt = manager.get_dream_prompt()
        # Should mention consolidation or memory or similar concepts
        lower_prompt = prompt.lower()
        assert any(
            term in lower_prompt for term in ["consolidat", "memory", "memor", "learn", "session"]
        )

    def test_save_load_round_trip(self, tmp_path: Path):
        """Test save and load state persistence."""
        state_file = tmp_path / "dream_state.json"

        # Create manager and modify state
        manager1 = AutoDreamManager(state_path=state_file)
        manager1.record_session()
        manager1.record_session()
        manager1.record_dream()

        # Save state
        manager1.save()

        # Load into new manager
        manager2 = AutoDreamManager(state_path=state_file)
        manager2.load()

        # Verify state matches
        assert manager2.state.session_count == manager1.state.session_count
        assert manager2.state.dream_count == manager1.state.dream_count
        assert manager2.state.last_dream_time == manager1.state.last_dream_time

    def test_save_creates_file(self, tmp_path: Path):
        """Test save creates state file."""
        state_file = tmp_path / "dream_state.json"
        assert not state_file.exists()

        manager = AutoDreamManager(state_path=state_file)
        manager.save()

        assert state_file.exists()

    def test_load_nonexistent_file(self, tmp_path: Path):
        """Test load handles nonexistent file gracefully."""
        state_file = tmp_path / "nonexistent.json"
        manager = AutoDreamManager(state_path=state_file)

        # Should not raise an error
        manager.load()

        # Should have default state
        assert manager.state.session_count == 0
        assert manager.state.dream_count == 0
        assert manager.state.last_dream_time is None

    def test_save_state_format(self, tmp_path: Path):
        """Test saved state has correct JSON format."""
        state_file = tmp_path / "dream_state.json"

        manager = AutoDreamManager(state_path=state_file)
        manager.record_session()
        manager.record_dream()
        manager.save()

        # Read and verify JSON structure
        with open(state_file) as f:
            data = json.load(f)

        assert "last_dream_time" in data
        assert "session_count" in data
        assert "dream_count" in data
        assert isinstance(data["session_count"], int)
        assert isinstance(data["dream_count"], int)


@pytest.mark.asyncio
async def test_run_auto_dream_defaults_to_review_without_retrieval_visible_write(tmp_path: Path):
    state_file = tmp_path / "dream_state.json"
    memory_dir = tmp_path / "origin-project" / ".koder" / "memory"
    candidate_store = CandidateStore(tmp_path / "memory-candidates", kind="memory")
    manager = AutoDreamManager(
        config=DreamConfig(cooldown_minutes=0, min_sessions=1),
        state_path=state_file,
    )
    manager.record_session()

    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "Use tmux scenarios for TUI verification.",
                "description": "Validation preference",
            }
        ],
        errors=[],
    )

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "remember tmux verification"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
            memory_candidate_store=candidate_store,
        )

    assert result.memories_written == 0
    assert result.memory_candidates_staged == 1
    assert result.errors == []
    assert result.saved_path is None
    assert len(candidate_store.list()) == 1
    candidate = candidate_store.list()[0]
    assert candidate.storage_scope == "project"
    assert candidate.origin_project_root == str((tmp_path / "origin-project").resolve())
    assert candidate.origin_session_id == "auto-dream-test-session"
    retrieval = retrieve_relevant_memories("tmux verification", [memory_dir], max_tokens=1000)
    assert retrieval.memories == []
    assert manager.state.dream_count == 1
    assert manager.state.session_count == 0
    assert state_file.exists()


@pytest.mark.asyncio
async def test_run_auto_dream_automatic_writes_memory_file(tmp_path: Path):
    manager = AutoDreamManager(
        config=DreamConfig(cooldown_minutes=0, min_sessions=1, write_mode="automatic"),
        state_path=tmp_path / "dream_state.json",
    )
    manager.record_session()
    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "Use tmux scenarios for TUI verification.",
                "description": "Validation preference",
            }
        ],
        errors=[],
    )

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "remember tmux verification"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
        )

    assert result.memories_written == 1
    assert result.memory_candidates_staged == 0
    assert result.saved_path is not None
    assert "Use tmux scenarios for TUI verification." in result.saved_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_automatic_mode_groups_actual_types_and_scopes_without_cross_project_leak(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    home.mkdir()
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setenv("HOME", str(home))
    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "projectalphazxq",
                "description": "Project-only fact",
            },
            {
                "type": "user",
                "content": "globaluserqvx",
                "description": "Cross-project user fact",
            },
            {
                "type": "feedback",
                "content": "feedback alpha governance marker",
                "description": "Project-local correction",
            },
            {
                "type": "reference",
                "content": "reference alpha governance marker",
                "description": "Project-local reference",
            },
        ],
        errors=[],
    )

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "persist governed memories"}],
            manager=AutoDreamManager(config=DreamConfig(write_mode="automatic")),
            origin_project_root=project_a,
            origin_session_id="project-a-session",
        )

    project_files = sorted((project_a / ".koder" / "memory").glob("*.md"))
    user_files = sorted((home / ".koder" / "memory").glob("*.md"))
    assert result.memories_written == 4
    assert set(result.saved_paths) == set(project_files + user_files)
    assert len(project_files) == 3
    assert len(user_files) == 1
    project_metadata = [
        parse_memory_file(path.read_text(encoding="utf-8")) for path in project_files
    ]
    user_metadata = [parse_memory_file(path.read_text(encoding="utf-8")) for path in user_files]
    assert {parsed.memory_type for parsed in project_metadata} == {
        "feedback",
        "project",
        "reference",
    }
    assert {parsed.metadata["storage_scope"] for parsed in project_metadata} == {"project"}
    assert user_metadata[0].memory_type == "user"
    assert user_metadata[0].metadata["storage_scope"] == "user"
    assert all(
        "origin_project_root" not in parsed.metadata for parsed in project_metadata + user_metadata
    )

    project_from_b = retrieve_relevant_memories(
        "projectalphazxq",
        [project_b / ".koder" / "memory", home / ".koder" / "memory"],
        max_tokens=1000,
    )
    user_from_b = retrieve_relevant_memories(
        "globaluserqvx",
        [project_b / ".koder" / "memory", home / ".koder" / "memory"],
        max_tokens=1000,
    )
    assert project_from_b.memories == []
    assert [memory.path for memory in user_from_b.memories] == user_files


@pytest.mark.asyncio
async def test_run_auto_dream_off_skips_extraction_and_writes(tmp_path: Path):
    manager = AutoDreamManager(config=DreamConfig(write_mode="off"))

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
    ) as extract:
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "secret"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
            memory_candidate_store=CandidateStore(tmp_path / "memory-candidates", kind="memory"),
        )

    extract.assert_not_awaited()
    assert result.memories_written == 0
    assert result.memory_candidates_staged == 0
    assert not (tmp_path / "origin-project" / ".koder" / "memory").exists()


@pytest.mark.asyncio
async def test_run_auto_dream_stages_skill_candidates_separately(tmp_path: Path):
    memory_store = CandidateStore(tmp_path / "memory-candidates", kind="memory")
    skill_store = CandidateStore(tmp_path / "skill-candidates", kind="skill")
    manager = AutoDreamManager(config=DreamConfig(write_mode="review"))
    extraction = ExtractionResult(
        memories=[{"type": "project", "content": "Use uv.", "description": "Tooling"}],
        skill_candidates=[
            {
                "name": "verify-first",
                "description": "Run focused verification",
                "instructions": "Run focused tests before broader suites.",
            }
        ],
        errors=[],
    )

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "Use uv and verify first"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
            memory_candidate_store=memory_store,
            skill_candidate_store=skill_store,
        )

    assert result.memory_candidates_staged == 1
    assert result.skill_candidates_staged == 1
    assert len(memory_store.list()) == 1
    assert len(skill_store.list()) == 1
    assert memory_store.list()[0].kind == "memory"
    assert skill_store.list()[0].kind == "skill"


@pytest.mark.asyncio
async def test_run_auto_dream_from_messages_records_task_state(tmp_path: Path):
    task_storage = TaskStorage(tmp_path / "tasks")
    manager = AutoDreamManager(
        config=DreamConfig(cooldown_minutes=0, min_sessions=1, write_mode="automatic"),
        state_path=tmp_path / "dream_state.json",
    )
    manager.record_session()

    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "Prefer scenario-backed TUI proof.",
                "description": "Validation preference",
            }
        ],
        errors=[],
    )

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "record task state"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
            task_storage=task_storage,
        )

    assert result.task_id is not None
    task = task_storage.get(result.task_id)
    assert task is not None
    assert task.title == "AutoDream memory consolidation"
    assert task.status == "completed"
    assert task.metadata["kind"] == "auto-dream"
    assert task.metadata["memories_written"] == 1
    assert task.metadata["errors"] == []
    assert task.metadata["saved_path"] == str(result.saved_path)
    assert list_auto_dream_tasks(storage=task_storage)[0].id == result.task_id


@pytest.mark.asyncio
async def test_run_auto_dream_from_messages_records_attempt_without_memories(tmp_path: Path):
    manager = AutoDreamManager(
        config=DreamConfig(cooldown_minutes=0, min_sessions=1, write_mode="automatic"),
        state_path=tmp_path / "dream_state.json",
    )
    manager.record_session()

    extraction = ExtractionResult(memories=[], errors=["model unavailable"])

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "nothing durable"}],
            manager=manager,
            **_origin_kwargs(tmp_path),
        )

    assert result.saved_path is None
    assert result.memories_written == 0
    assert result.errors == ["extraction_error: model unavailable"]
    assert manager.state.dream_count == 1
    assert manager.state.session_count == 0


@pytest.mark.asyncio
async def test_secret_memory_is_not_persisted_in_review_mode(tmp_path: Path):
    store = CandidateStore(tmp_path / "memory-candidates", kind="memory")
    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "credential sk-live-EXAMPLE-SECRET",
                "description": "must reject",
            }
        ],
        errors=[],
    )
    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "secret"}],
            manager=AutoDreamManager(config=DreamConfig(write_mode="review")),
            **_origin_kwargs(tmp_path),
            memory_candidate_store=store,
        )

    assert store.list() == []
    assert result.memory_candidates_staged == 0
    assert all("sk-live" not in error for error in result.errors)


@pytest.mark.asyncio
async def test_secret_memory_is_not_persisted_in_automatic_mode(tmp_path: Path):
    extraction = ExtractionResult(
        memories=[
            {
                "type": "project",
                "content": "credential sk-live-EXAMPLE-SECRET",
                "description": "must reject",
            }
        ],
        errors=[],
    )
    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        result = await run_auto_dream_from_messages(
            [{"role": "user", "content": "secret"}],
            manager=AutoDreamManager(config=DreamConfig(write_mode="automatic")),
            **_origin_kwargs(tmp_path),
        )

    assert result.saved_path is None
    assert not (tmp_path / "origin-project" / ".koder" / "memory").exists()
    assert all("sk-live" not in error for error in result.errors)


@pytest.mark.asyncio
async def test_provider_error_secret_is_redacted_from_task_metadata(tmp_path: Path):
    storage = TaskStorage(tmp_path / "tasks")
    secret = "sk-live-EXAMPLE-SECRET"
    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        side_effect=RuntimeError(f"provider failed with {secret}"),
    ):
        with pytest.raises(RuntimeError):
            await run_auto_dream_from_messages(
                [{"role": "user", "content": "trigger"}],
                manager=AutoDreamManager(config=DreamConfig(write_mode="review")),
                **_origin_kwargs(tmp_path),
                task_storage=storage,
            )

    task = storage.list_all()[0]
    assert task.status == "failed"
    assert secret not in json.dumps(task.to_dict())
    assert "[REDACTED]" in json.dumps(task.to_dict())


@pytest.mark.asyncio
async def test_automatic_runs_in_same_second_do_not_overwrite(tmp_path: Path):
    extraction = ExtractionResult(
        memories=[{"type": "project", "content": "durable", "description": "same second"}],
        errors=[],
    )
    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        new_callable=AsyncMock,
        return_value=extraction,
    ):
        results = [
            await run_auto_dream_from_messages(
                [{"role": "user", "content": "trigger"}],
                manager=AutoDreamManager(config=DreamConfig(write_mode="automatic")),
                **_origin_kwargs(tmp_path),
            )
            for _ in range(2)
        ]

    assert results[0].saved_path != results[1].saved_path
    assert len(list((tmp_path / "origin-project" / ".koder" / "memory").glob("*.md"))) == 2


@pytest.mark.asyncio
async def test_timeout_marks_auto_dream_task_terminal(tmp_path: Path):
    storage = TaskStorage(tmp_path / "tasks")

    async def slow_extraction(_messages):
        await asyncio.sleep(10)

    with patch(
        "koder_agent.harness.memory.auto_dream.llm_extract_memories",
        side_effect=slow_extraction,
    ):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                run_auto_dream_from_messages(
                    [{"role": "user", "content": "trigger"}],
                    manager=AutoDreamManager(config=DreamConfig(write_mode="review")),
                    **_origin_kwargs(tmp_path),
                    task_storage=storage,
                ),
                timeout=0.01,
            )

    task = storage.list_all()[0]
    assert task.status == "cancelled"
    assert task.metadata["failure_code"] == "cancelled"


@pytest.mark.asyncio
async def test_persistence_failure_marks_auto_dream_task_terminal(tmp_path: Path):
    storage = TaskStorage(tmp_path / "tasks")
    extraction = ExtractionResult(
        memories=[{"type": "project", "content": "durable", "description": "write"}],
        errors=[],
    )
    with (
        patch(
            "koder_agent.harness.memory.auto_dream.llm_extract_memories",
            new_callable=AsyncMock,
            return_value=extraction,
        ),
        patch(
            "koder_agent.harness.memory.auto_dream.write_approved_output",
            side_effect=OSError("disk failed"),
        ),
    ):
        with pytest.raises(OSError, match="disk failed"):
            await run_auto_dream_from_messages(
                [{"role": "user", "content": "trigger"}],
                manager=AutoDreamManager(config=DreamConfig(write_mode="automatic")),
                **_origin_kwargs(tmp_path),
                task_storage=storage,
            )

    task = storage.list_all()[0]
    assert task.status == "failed"
    assert task.metadata["failure_code"] == "persistence_failure"


def test_startup_reconciles_stale_auto_dream_task(tmp_path: Path):
    storage = TaskStorage(tmp_path / "tasks")
    task = storage.create(
        "AutoDream memory consolidation",
        metadata={"kind": "auto-dream", "started_at": datetime.now(timezone.utc).isoformat()},
    )
    storage.update(task.id, status="in_progress")

    count = reconcile_stale_auto_dream_tasks(
        storage,
        now=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    reconciled = storage.get(task.id)
    assert count == 1
    assert reconciled is not None
    assert reconciled.status == "failed"
    assert reconciled.metadata["failure_code"] == "interrupted"


def test_malformed_task_record_does_not_block_stale_auto_dream_reconciliation(
    tmp_path: Path,
    caplog,
):
    storage = TaskStorage(tmp_path / "tasks")
    task = storage.create(
        "AutoDream memory consolidation",
        metadata={"kind": "auto-dream", "started_at": datetime.now(timezone.utc).isoformat()},
    )
    storage.update(task.id, status="in_progress")
    (storage.root / "broken.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        count = reconcile_stale_auto_dream_tasks(
            storage,
            now=datetime.now(timezone.utc) + timedelta(hours=2),
        )

    reconciled = storage.get(task.id)
    assert count == 1
    assert reconciled is not None
    assert reconciled.status == "failed"
    assert reconciled.metadata["failure_code"] == "interrupted"
    assert "broken.json" in caplog.text
    assert (storage.root / "broken.json").read_text(encoding="utf-8") == "{not json"
