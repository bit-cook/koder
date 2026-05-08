"""Tests for AutoDream background memory consolidation."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.auto_dream import (
    AutoDreamManager,
    DreamConfig,
    DreamState,
    list_auto_dream_tasks,
    run_auto_dream_from_messages,
)
from koder_agent.harness.memory.extraction import ExtractionResult
from koder_agent.harness.tasks.storage import TaskStorage


class TestDreamConfig:
    """Test DreamConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DreamConfig()
        assert config.enabled is True
        assert config.cooldown_minutes == 60
        assert config.min_sessions == 3

    def test_custom_values(self):
        """Test custom configuration values."""
        config = DreamConfig(
            enabled=False,
            cooldown_minutes=30,
            min_sessions=5,
        )
        assert config.enabled is False
        assert config.cooldown_minutes == 30
        assert config.min_sessions == 5


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
async def test_run_auto_dream_from_messages_writes_memory_file(tmp_path: Path):
    state_file = tmp_path / "dream_state.json"
    memory_dir = tmp_path / "memory"
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
            memory_dir=memory_dir,
        )

    assert result.memories_written == 1
    assert result.errors == []
    assert result.saved_path is not None
    saved = result.saved_path.read_text(encoding="utf-8")
    assert "AutoDream consolidated session memories" in saved
    assert "Use tmux scenarios for TUI verification." in saved
    assert manager.state.dream_count == 1
    assert manager.state.session_count == 0
    assert state_file.exists()


@pytest.mark.asyncio
async def test_run_auto_dream_from_messages_records_task_state(tmp_path: Path):
    task_storage = TaskStorage(tmp_path / "tasks")
    manager = AutoDreamManager(
        config=DreamConfig(cooldown_minutes=0, min_sessions=1),
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
            memory_dir=tmp_path / "memory",
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
        config=DreamConfig(cooldown_minutes=0, min_sessions=1),
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
            memory_dir=tmp_path / "memory",
        )

    assert result.saved_path is None
    assert result.memories_written == 0
    assert result.errors == ["model unavailable"]
    assert manager.state.dream_count == 1
    assert manager.state.session_count == 0
