"""AutoDream - Background memory consolidation for Koder.

AutoDream periodically consolidates session memories by reviewing recent
interactions and extracting key learnings. This helps maintain long-term
context and improves the quality of assistance over time.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from koder_agent.harness.tasks.models import TaskRecord
from koder_agent.harness.tasks.storage import TaskStorage

from .approved_writer import read_trusted_file, write_approved_output
from .candidates import (
    CandidateStore,
    default_memory_candidate_store,
    default_skill_candidate_store,
    memory_storage_scope,
    normalize_candidate_origin,
)
from .extraction import llm_extract_memories
from .governance import (
    sanitize_text,
    sanitized_error,
    validate_memory_payload,
    validate_skill_payload,
)
from .memory_files import render_memory_file

_AUTO_DREAM_TASK_MAX_BYTES = 64 * 1024
AUTO_DREAM_STALE_AFTER_SECONDS = 60 * 60
logger = logging.getLogger(__name__)


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
    write_mode: Literal["off", "review", "automatic"] = "review"

    def __post_init__(self) -> None:
        if not isinstance(self.write_mode, str) or self.write_mode not in {
            "off",
            "review",
            "automatic",
        }:
            raise ValueError("write_mode must be off, review, or automatic")


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
    memory_candidates_staged: int = 0
    skill_candidates_staged: int = 0
    saved_paths: tuple[Path, ...] = ()


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

    tasks, errors = _read_auto_dream_task_records(root)
    return sorted(tasks, key=lambda task: task.updated_at, reverse=True)[:limit], errors


def _read_auto_dream_task_records(root: Path) -> tuple[list[TaskRecord], list[str]]:
    """Parse bounded task records independently so one malformed file cannot abort a scan."""

    tasks: list[TaskRecord] = []
    errors: list[str] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(
                read_trusted_file(
                    root,
                    path.name,
                    maximum_bytes=_AUTO_DREAM_TASK_MAX_BYTES,
                ).decode("utf-8")
            )
            tasks.append(TaskRecord.from_dict(data))
        except Exception as exc:
            errors.append(
                f"{sanitize_text(path.name, limit=128)}: "
                f"{sanitized_error(exc, code='invalid_task_record')}"
            )
    return tasks, errors


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
            "pid": os.getpid(),
        },
    )
    if storage.update(task.id, status="in_progress") is None:
        raise RuntimeError("failed to persist AutoDream in-progress task state")
    return task.id


def _finish_auto_dream_task(
    storage: TaskStorage,
    task_id: str,
    *,
    saved_path: Path | None,
    memories_written: int,
    memory_candidates_staged: int,
    skill_candidates_staged: int,
    errors: list[str],
    saved_paths: list[Path] | tuple[Path, ...] = (),
    status: Literal["completed", "failed", "cancelled"] = "completed",
    failure_code: str | None = None,
) -> None:
    metadata = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "saved_path": str(saved_path) if saved_path else None,
        "saved_paths": [str(path) for path in saved_paths],
        "memories_written": memories_written,
        "memory_candidates_staged": memory_candidates_staged,
        "skill_candidates_staged": skill_candidates_staged,
        "errors": [sanitize_text(error, limit=320) for error in errors[:20]],
        "failure_code": failure_code,
    }
    updated = storage.update(
        task_id,
        status=status,
        metadata=metadata,
    )
    if updated is None:
        raise RuntimeError("failed to persist terminal AutoDream task state")


def reconcile_stale_auto_dream_tasks(
    storage: TaskStorage,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = AUTO_DREAM_STALE_AFTER_SECONDS,
) -> int:
    """Mark abandoned in-progress AutoDream tasks terminal after restart."""

    current = now or datetime.now(timezone.utc)
    reconciled = 0
    tasks, malformed = _read_auto_dream_task_records(storage.root)
    for error in malformed:
        logger.warning("Skipping malformed AutoDream task record during reconciliation: %s", error)
    for task in tasks:
        if task.status != "in_progress" or (task.metadata or {}).get("kind") != "auto-dream":
            continue
        try:
            updated_at = datetime.fromisoformat(task.updated_at)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age = (current - updated_at).total_seconds()
        except (TypeError, ValueError):
            age = stale_after_seconds
        if age < stale_after_seconds:
            continue
        _finish_auto_dream_task(
            storage,
            task.id,
            saved_path=None,
            memories_written=0,
            memory_candidates_staged=0,
            skill_candidates_staged=0,
            saved_paths=(),
            errors=["interrupted: stale AutoDream task recovered at startup"],
            status="failed",
            failure_code="interrupted",
        )
        reconciled += 1
    return reconciled


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

        if self.config.write_mode == "off":
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


def _automatic_memory_target(
    *,
    storage_scope: Literal["project", "user"],
    origin_project_root: str,
) -> Path:
    if storage_scope == "user":
        return Path.home() / ".koder" / "memory"
    project_root = Path(origin_project_root)
    if not project_root.is_dir() or Path(os.path.realpath(project_root)) != project_root:
        raise ValueError("AutoDream origin project root is unavailable")
    return project_root / ".koder" / "memory"


async def run_auto_dream_from_messages(
    messages: list[dict],
    *,
    manager: AutoDreamManager,
    origin_project_root: str | Path,
    origin_session_id: str,
    task_storage: TaskStorage | None = None,
    memory_candidate_store: CandidateStore | None = None,
    skill_candidate_store: CandidateStore | None = None,
) -> DreamRunResult:
    """Extract durable memories from recent session messages and persist them."""

    if manager.config.write_mode == "off" or not manager.config.enabled:
        manager.save()
        return DreamRunResult(saved_path=None, memories_written=0, errors=[])

    normalized_project_root, normalized_session_id = normalize_candidate_origin(
        origin_project_root,
        origin_session_id,
    )

    task_id: str | None = None
    if task_storage is not None:
        task_id = _start_auto_dream_task(
            task_storage,
            manager=manager,
            message_count=len(messages),
        )

    saved_path: Path | None = None
    saved_paths: list[Path] = []
    memories_written = 0
    memory_candidates_staged = 0
    skill_candidates_staged = 0
    errors: list[str] = []
    try:
        extraction = await llm_extract_memories(messages)
        errors.extend(
            sanitized_error(error, code="extraction_error") for error in extraction.errors
        )

        governed_memories: list[dict[str, str]] = []
        for memory in extraction.memories:
            try:
                governed_memories.append(validate_memory_payload(memory))
            except ValueError as exc:
                errors.append(sanitized_error(exc, code="governance_rejection"))

        governed_skills: list[dict[str, str]] = []
        for candidate in getattr(extraction, "skill_candidates", []):
            try:
                governed_skills.append(validate_skill_payload(candidate))
            except ValueError as exc:
                errors.append(sanitized_error(exc, code="governance_rejection"))

        if governed_memories and manager.config.write_mode == "automatic":
            groups: dict[tuple[str, str], list[dict[str, str]]] = {}
            for memory in governed_memories:
                key = (memory_storage_scope(memory), memory["type"])
                groups.setdefault(key, []).append(memory)
            for (storage_scope, memory_type), grouped_memories in groups.items():
                target_dir = _automatic_memory_target(
                    storage_scope=storage_scope,
                    origin_project_root=normalized_project_root,
                )
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                filename = f"auto-dream-{memory_type}-{timestamp}-{uuid.uuid4().hex}.md"
                saved_paths.append(
                    write_approved_output(
                        target_dir,
                        filename,
                        render_memory_file(
                            memory_type=memory_type,
                            description=(
                                f"AutoDream consolidated {storage_scope}-scoped "
                                f"{memory_type} memories"
                            ),
                            body=_format_memory_body(grouped_memories),
                            metadata={
                                "storage_scope": storage_scope,
                            },
                        ),
                        exclusive=True,
                    )
                )
                memories_written += len(grouped_memories)
            saved_path = saved_paths[0] if saved_paths else None
        elif governed_memories:
            memory_store = memory_candidate_store or default_memory_candidate_store()
            for memory in governed_memories:
                record = memory_store.stage(
                    memory,
                    storage_scope=memory_storage_scope(memory),
                    origin_project_root=normalized_project_root,
                    origin_session_id=normalized_session_id,
                )
                if record.state == "pending":
                    memory_candidates_staged += 1

        if governed_skills:
            skill_store = skill_candidate_store or default_skill_candidate_store()
            for candidate in governed_skills:
                record = skill_store.stage(
                    candidate,
                    storage_scope="user",
                    origin_project_root=normalized_project_root,
                    origin_session_id=normalized_session_id,
                )
                if record.state == "pending":
                    skill_candidates_staged += 1

        manager.record_dream()
        manager.save()
    except asyncio.CancelledError:
        if task_storage is not None and task_id is not None:
            _finish_auto_dream_task(
                task_storage,
                task_id,
                saved_path=saved_path,
                memories_written=memories_written,
                memory_candidates_staged=memory_candidates_staged,
                skill_candidates_staged=skill_candidates_staged,
                saved_paths=saved_paths,
                errors=["cancelled: AutoDream shutdown deadline reached"],
                status="cancelled",
                failure_code="cancelled",
            )
        raise
    except Exception as exc:
        errors.append(sanitized_error(exc, code="persistence_failure"))
        if task_storage is not None and task_id is not None:
            try:
                _finish_auto_dream_task(
                    task_storage,
                    task_id,
                    saved_path=saved_path,
                    memories_written=memories_written,
                    memory_candidates_staged=memory_candidates_staged,
                    skill_candidates_staged=skill_candidates_staged,
                    saved_paths=saved_paths,
                    errors=errors,
                    status="failed",
                    failure_code="persistence_failure",
                )
            except Exception:
                pass
        raise

    if task_storage is not None and task_id is not None:
        try:
            _finish_auto_dream_task(
                task_storage,
                task_id,
                saved_path=saved_path,
                memories_written=memories_written,
                memory_candidates_staged=memory_candidates_staged,
                skill_candidates_staged=skill_candidates_staged,
                saved_paths=saved_paths,
                errors=errors,
            )
        except Exception as exc:
            try:
                _finish_auto_dream_task(
                    task_storage,
                    task_id,
                    saved_path=saved_path,
                    memories_written=memories_written,
                    memory_candidates_staged=memory_candidates_staged,
                    skill_candidates_staged=skill_candidates_staged,
                    saved_paths=saved_paths,
                    errors=[sanitized_error(exc, code="task_persistence_failure")],
                    status="failed",
                    failure_code="task_persistence_failure",
                )
            except Exception:
                pass
            raise
    return DreamRunResult(
        saved_path=saved_path,
        memories_written=memories_written,
        errors=errors,
        task_id=task_id,
        memory_candidates_staged=memory_candidates_staged,
        skill_candidates_staged=skill_candidates_staged,
        saved_paths=tuple(saved_paths),
    )
