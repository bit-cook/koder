"""AutoDream - Background memory consolidation for Koder.

AutoDream periodically consolidates session memories by reviewing recent
interactions and extracting key learnings. This helps maintain long-term
context and improves the quality of assistance over time.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from koder_agent.harness.tasks.models import TaskRecord
from koder_agent.harness.tasks.storage import TaskStorage

from .extraction import llm_extract_memories
from .memory_files import save_memory_file


@dataclass
class DreamConfig:
    """Configuration for AutoDream memory consolidation.

    Attributes:
        enabled: Whether AutoDream is enabled
        cooldown_minutes: Minimum minutes between dream sessions
        min_sessions: Minimum number of sessions before triggering a dream
    """

    enabled: bool = True
    cooldown_minutes: int = 60
    min_sessions: int = 3


@dataclass
class DreamState:
    """State tracking for AutoDream.

    Attributes:
        last_dream_time: Timestamp of last dream session (None if never)
        session_count: Number of sessions since last dream
        dream_count: Total number of dreams completed
    """

    last_dream_time: Optional[float] = None
    session_count: int = 0
    dream_count: int = 0


@dataclass(frozen=True)
class DreamRunResult:
    """Result of one AutoDream consolidation attempt."""

    saved_path: Path | None
    memories_written: int
    errors: list[str]
    task_id: str | None = None


def default_auto_dream_task_storage() -> TaskStorage:
    """Return the Koder-owned storage used for AutoDream task records."""

    return TaskStorage(Path.home() / ".koder" / "tasks" / "auto-dream")


def list_auto_dream_tasks(*, storage: TaskStorage | None = None, limit: int = 5) -> list:
    """Return recent AutoDream task records for runtime status views."""

    tasks, _errors = list_auto_dream_tasks_with_errors(storage=storage, limit=limit)
    return tasks


def list_auto_dream_tasks_with_errors(
    *, storage: TaskStorage | None = None, limit: int = 5
) -> tuple[list[TaskRecord], list[str]]:
    """Return recent AutoDream task records plus malformed-record diagnostics."""

    if storage is None:
        root = Path.home() / ".koder" / "tasks" / "auto-dream"
        if not root.exists():
            return [], []
    else:
        root = storage.root

    tasks: list[TaskRecord] = []
    errors: list[str] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tasks.append(TaskRecord.from_dict(data))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return sorted(tasks, key=lambda task: task.updated_at, reverse=True)[:limit], errors


def _start_auto_dream_task(
    storage: TaskStorage,
    *,
    manager: "AutoDreamManager",
    message_count: int,
) -> str:
    task = storage.create(
        "AutoDream memory consolidation",
        description="Consolidate durable memories from recent Koder sessions.",
        metadata={
            "kind": "auto-dream",
            "session_count": manager.state.session_count,
            "message_count": message_count,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    storage.update(task.id, status="in_progress")
    return task.id


def _finish_auto_dream_task(
    storage: TaskStorage,
    task_id: str,
    *,
    saved_path: Path | None,
    memories_written: int,
    errors: list[str],
) -> None:
    storage.update(
        task_id,
        status="completed",
        metadata={
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "saved_path": str(saved_path) if saved_path else None,
            "memories_written": memories_written,
            "errors": list(errors),
        },
    )


class AutoDreamManager:
    """Manager for AutoDream background memory consolidation.

    This class handles the logic for determining when to trigger memory
    consolidation, tracking state, and generating consolidation prompts.
    """

    def __init__(
        self,
        config: Optional[DreamConfig] = None,
        state_path: Optional[Path] = None,
    ):
        """Initialize AutoDreamManager.

        Args:
            config: Configuration for AutoDream. Uses defaults if None.
            state_path: Path to persist state. No persistence if None.
        """
        self.config = config or DreamConfig()
        self.state = DreamState()
        self.state_path = state_path

        if self.state_path:
            self.load()

    def should_dream(self) -> bool:
        """Check if conditions are met to trigger a dream session.

        Returns:
            True if AutoDream should run, False otherwise
        """
        # Check if AutoDream is enabled
        if not self.config.enabled:
            return False

        # Check if we have enough sessions
        if self.state.session_count < self.config.min_sessions:
            return False

        # If never dreamed before, allow it
        if self.state.last_dream_time is None:
            return True

        # Check if cooldown period has passed
        cooldown_seconds = self.config.cooldown_minutes * 60
        time_since_last_dream = time.time() - self.state.last_dream_time

        return time_since_last_dream >= cooldown_seconds

    def record_session(self) -> None:
        """Record that a session has occurred.

        Increments the session count for tracking when to trigger dreams.
        """
        self.state.session_count += 1

    def record_dream(self) -> None:
        """Record that a dream session has completed.

        Updates state to reflect the completed dream:
        - Sets last_dream_time to current time
        - Increments dream_count
        - Resets session_count to 0
        """
        self.state.last_dream_time = time.time()
        self.state.dream_count += 1
        self.state.session_count = 0

    def get_dream_prompt(self) -> str:
        """Generate the prompt for memory consolidation.

        Returns:
            A prompt asking the LLM to review and consolidate memories
        """
        return """Review recent session memories and consolidate key learnings.

Analyze the conversation history from recent sessions and extract:
1. Patterns in user preferences and working style
2. Common tasks and workflows
3. Technical decisions and architectural choices
4. Important context about the codebase
5. Lessons learned from mistakes or corrections

Consolidate these into concise, actionable insights that will improve
future assistance. Focus on information that has long-term value."""

    def save(self) -> None:
        """Persist current state to disk.

        Saves state as JSON to the configured state_path.
        Does nothing if state_path is None.
        """
        if not self.state_path:
            return

        # Ensure parent directory exists
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize state to JSON
        state_dict = {
            "last_dream_time": self.state.last_dream_time,
            "session_count": self.state.session_count,
            "dream_count": self.state.dream_count,
        }

        with open(self.state_path, "w") as f:
            json.dump(state_dict, f, indent=2)

    def load(self) -> None:
        """Load state from disk.

        Restores state from JSON at the configured state_path.
        Does nothing if state_path is None or file doesn't exist.
        """
        if not self.state_path or not self.state_path.exists():
            return

        try:
            with open(self.state_path) as f:
                state_dict = json.load(f)

            self.state = DreamState(
                last_dream_time=state_dict.get("last_dream_time"),
                session_count=state_dict.get("session_count", 0),
                dream_count=state_dict.get("dream_count", 0),
            )
        except (json.JSONDecodeError, OSError):
            # If loading fails, keep default state
            pass


def _format_memory_body(memories: list[dict]) -> str:
    lines = ["# AutoDream Consolidation", ""]
    for memory in memories:
        memory_type = memory.get("type") or "reference"
        content = str(memory.get("content") or "").strip()
        description = str(memory.get("description") or "").strip()
        if not content:
            continue
        if description:
            lines.append(f"- [{memory_type}] {content} ({description})")
        else:
            lines.append(f"- [{memory_type}] {content}")
    return "\n".join(lines).strip()


async def run_auto_dream_from_messages(
    messages: list[dict],
    *,
    manager: AutoDreamManager,
    memory_dir: Path | None = None,
    task_storage: TaskStorage | None = None,
) -> DreamRunResult:
    """Extract durable memories from recent session messages and persist them."""

    task_id: str | None = None
    if task_storage is not None:
        task_id = _start_auto_dream_task(
            task_storage,
            manager=manager,
            message_count=len(messages),
        )

    try:
        extraction = await llm_extract_memories(messages)
    except Exception as exc:
        errors = [str(exc)]
        if task_storage is not None and task_id is not None:
            _finish_auto_dream_task(
                task_storage,
                task_id,
                saved_path=None,
                memories_written=0,
                errors=errors,
            )
        raise

    saved_path: Path | None = None
    if extraction.memories:
        target_dir = memory_dir or Path.home() / ".koder" / "memory"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        saved_path = save_memory_file(
            target_dir / f"auto-dream-{timestamp}.md",
            memory_type="project",
            description="AutoDream consolidated session memories",
            body=_format_memory_body(extraction.memories),
        )

    manager.record_dream()
    manager.save()
    if task_storage is not None and task_id is not None:
        _finish_auto_dream_task(
            task_storage,
            task_id,
            saved_path=saved_path,
            memories_written=len(extraction.memories),
            errors=list(extraction.errors),
        )
    return DreamRunResult(
        saved_path=saved_path,
        memories_written=len(extraction.memories),
        errors=list(extraction.errors),
        task_id=task_id,
    )
