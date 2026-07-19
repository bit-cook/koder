"""Durable, review-gated queues for AutoDream memory and skill candidates."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

import yaml

from .approved_writer import (
    list_trusted_names,
    open_trusted_directory,
    read_trusted_file,
    replace_trusted_file,
    trusted_file_exists,
    trusted_file_size,
    unlink_trusted_file,
    write_approved_output,
)
from .governance import (
    MAX_CANDIDATE_COUNT,
    MAX_CANDIDATE_FILE_BYTES,
    MAX_CANDIDATE_QUEUE_BYTES,
    validate_candidate_payload,
)
from .memory_files import render_memory_file

logger = logging.getLogger(__name__)

CandidateKind = Literal["memory", "skill"]
CandidateStorageScope = Literal["project", "user"]
_CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{64}")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_RECEIPT_MAX_BYTES = 16 * 1024
_ORIGIN_SESSION_MAX_CHARS = 256
_LOCK_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class CandidateRecord:
    """A review candidate persisted independently from active memory."""

    id: str
    kind: CandidateKind
    created_at: str
    storage_scope: CandidateStorageScope
    origin_project_root: str
    origin_session_id: str
    payload: dict[str, str]
    state: str = "pending"


@dataclass(frozen=True)
class CandidateApprovalResult:
    """Result of approving a candidate."""

    candidate_id: str
    kind: CandidateKind
    status: str
    output_path: Path | None


def _validate_candidate_id(candidate_id: str) -> str:
    if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError("invalid candidate id")
    return candidate_id


def _validate_storage_scope(value: object) -> CandidateStorageScope:
    if value not in {"project", "user"}:
        raise ValueError("candidate storage scope must be project or user")
    return value


def memory_storage_scope(payload: dict[str, str]) -> CandidateStorageScope:
    """Choose the only retrieval scope allowed for a governed memory type."""

    return "user" if payload["type"] == "user" else "project"


def _normalize_origin_project_root(value: str | Path) -> str:
    if not isinstance(value, (str, Path)):
        raise ValueError("candidate origin project root must be a path")
    raw = os.path.expanduser(str(value))
    if not raw or "\x00" in raw:
        raise ValueError("candidate origin project root is invalid")
    absolute = Path(os.path.abspath(raw))
    return str(Path(os.path.realpath(absolute)))


def _validate_origin_session_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("candidate origin session id must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > _ORIGIN_SESSION_MAX_CHARS:
        raise ValueError("candidate origin session id is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("candidate origin session id contains control characters")
    return normalized


def normalize_candidate_origin(
    project_root: str | Path,
    session_id: str,
) -> tuple[str, str]:
    """Normalize trusted runtime provenance before it enters an identity or artifact."""

    return (
        _normalize_origin_project_root(project_root),
        _validate_origin_session_id(session_id),
    )


def _json_bytes(data: dict) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _thread_lock(root: Path) -> threading.RLock:
    key = str(root)
    with _LOCK_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


class CandidateStore:
    """Filesystem-backed candidate store with content-derived identities."""

    def __init__(self, root: str | Path, *, kind: CandidateKind):
        if kind not in {"memory", "skill"}:
            raise ValueError("candidate kind must be memory or skill")
        self.root = Path(os.path.abspath(os.path.expanduser(str(root))))
        self.kind = kind
        if self.root.exists():
            self.reconcile()

    @property
    def pending_dir(self) -> Path:
        return self.root / "pending"

    @property
    def processing_dir(self) -> Path:
        return self.root / "processing"

    @property
    def approved_dir(self) -> Path:
        return self.root / "approved"

    @property
    def rejected_dir(self) -> Path:
        return self.root / "rejected"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize store operations across threads and processes."""

        with _thread_lock(self.root):
            with open_trusted_directory(self.root, create=True) as root_fd:
                lock_fd = os.open(
                    ".lock",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=root_fd,
                )
                try:
                    os.fchmod(lock_fd, 0o600)
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    yield
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)

    def _relative(self, state: str, candidate_id: str) -> Path:
        return Path(state) / f"{_validate_candidate_id(candidate_id)}.json"

    def _candidate_id(
        self,
        payload: dict[str, str],
        *,
        storage_scope: CandidateStorageScope,
        origin_project_root: str,
        origin_session_id: str,
    ) -> str:
        canonical = json.dumps(
            {
                "kind": self.kind,
                "payload": payload,
                "storage_scope": storage_scope,
                "origin_project_root": origin_project_root,
                "origin_session_id": origin_session_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _write_json(self, relative: Path, data: dict, *, exclusive: bool) -> None:
        serialized = _json_bytes(data)
        if len(serialized) > MAX_CANDIDATE_FILE_BYTES:
            raise ValueError("candidate record exceeds size limit")
        write_approved_output(self.root, relative, serialized, exclusive=exclusive)

    def _read_json(self, relative: Path, *, maximum_bytes: int) -> dict:
        raw = read_trusted_file(self.root, relative, maximum_bytes=maximum_bytes)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("candidate record must be an object")
        return data

    def _read(self, relative: Path, *, state: str) -> CandidateRecord:
        data = self._read_json(relative, maximum_bytes=MAX_CANDIDATE_FILE_BYTES)
        if set(data) != {
            "id",
            "kind",
            "created_at",
            "storage_scope",
            "origin_project_root",
            "origin_session_id",
            "payload",
        }:
            raise ValueError("candidate record has malformed fields")
        candidate_id = _validate_candidate_id(data.get("id", ""))
        created_at = data.get("created_at")
        if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
            raise ValueError("candidate record has invalid created_at")
        if candidate_id != relative.stem or data.get("kind") != self.kind:
            raise ValueError("candidate record identity does not match its store")
        payload = validate_candidate_payload(self.kind, data.get("payload"))
        storage_scope = _validate_storage_scope(data.get("storage_scope"))
        if self.kind == "memory" and storage_scope != memory_storage_scope(payload):
            raise ValueError("memory candidate storage scope does not match its type")
        if self.kind == "skill" and storage_scope != "user":
            raise ValueError("skill candidate storage scope must be user")
        origin_project_root = _normalize_origin_project_root(data.get("origin_project_root", ""))
        if origin_project_root != data.get("origin_project_root"):
            raise ValueError("candidate origin project root is not canonical")
        origin_session_id = _validate_origin_session_id(data.get("origin_session_id"))
        if (
            self._candidate_id(
                payload,
                storage_scope=storage_scope,
                origin_project_root=origin_project_root,
                origin_session_id=origin_session_id,
            )
            != candidate_id
        ):
            raise ValueError("candidate payload does not match candidate id")
        return CandidateRecord(
            id=candidate_id,
            kind=self.kind,
            created_at=created_at,
            storage_scope=storage_scope,
            origin_project_root=origin_project_root,
            origin_session_id=origin_session_id,
            payload=payload,
            state=state,
        )

    def _terminal_receipt_unlocked(self, candidate_id: str) -> str | None:
        approved = self._approval_receipt_unlocked(candidate_id)
        rejected = self._rejection_receipt_unlocked(candidate_id)
        if approved is not None and rejected is not None:
            raise ValueError("candidate has conflicting terminal receipts")
        if approved is not None:
            return "approved"
        if rejected is not None:
            return "rejected"
        return None

    def _reconcile_receipted_unlocked(self) -> None:
        candidate_ids: set[str] = set()
        for directory in ("pending", "processing"):
            for name in list_trusted_names(self.root, directory):
                if name.endswith(".json") and _CANDIDATE_ID_RE.fullmatch(name[:-5]):
                    candidate_ids.add(name[:-5])
        for candidate_id in sorted(candidate_ids):
            if self._terminal_receipt_unlocked(candidate_id) is None:
                continue
            for directory in ("pending", "processing"):
                try:
                    unlink_trusted_file(
                        self.root,
                        self._relative(directory, candidate_id),
                        missing_ok=True,
                    )
                except (OSError, ValueError):
                    logger.warning(
                        "Could not reap receipted %s candidate state id=%s",
                        self.kind,
                        candidate_id,
                    )

    def reconcile(self) -> None:
        """Reap pending/processing records that already have terminal receipts."""

        with self.locked():
            self._reconcile_receipted_unlocked()

    def _queue_usage(self) -> tuple[int, int]:
        count = 0
        total_bytes = 0
        for directory in ("pending", "processing"):
            for name in list_trusted_names(self.root, directory):
                if not name.endswith(".json"):
                    continue
                candidate_id = name[:-5]
                if _CANDIDATE_ID_RE.fullmatch(candidate_id):
                    if self._terminal_receipt_unlocked(candidate_id) is not None:
                        continue
                count += 1
                total_bytes += trusted_file_size(self.root, Path(directory) / name)
        return count, total_bytes

    def stage(
        self,
        payload: dict,
        *,
        storage_scope: CandidateStorageScope,
        origin_project_root: str | Path,
        origin_session_id: str,
    ) -> CandidateRecord:
        normalized = validate_candidate_payload(self.kind, payload)
        normalized_scope = _validate_storage_scope(storage_scope)
        if self.kind == "memory" and normalized_scope != memory_storage_scope(normalized):
            raise ValueError("memory candidate storage scope does not match its type")
        if self.kind == "skill" and normalized_scope != "user":
            raise ValueError("skill candidate storage scope must be user")
        normalized_project_root = _normalize_origin_project_root(origin_project_root)
        normalized_session_id = _validate_origin_session_id(origin_session_id)
        candidate_id = self._candidate_id(
            normalized,
            storage_scope=normalized_scope,
            origin_project_root=normalized_project_root,
            origin_session_id=normalized_session_id,
        )
        with self.locked():
            self._reconcile_receipted_unlocked()
            existing = self._get_unlocked(candidate_id)
            if existing is not None:
                return existing
            receipt = self._approval_receipt_unlocked(candidate_id)
            if receipt is not None:
                return CandidateRecord(
                    id=candidate_id,
                    kind=self.kind,
                    created_at=receipt["approved_at"],
                    storage_scope=normalized_scope,
                    origin_project_root=normalized_project_root,
                    origin_session_id=normalized_session_id,
                    payload=normalized,
                    state="approved",
                )
            rejection = self._rejection_receipt_unlocked(candidate_id)
            if rejection is not None:
                return CandidateRecord(
                    id=candidate_id,
                    kind=self.kind,
                    created_at=rejection["rejected_at"],
                    storage_scope=normalized_scope,
                    origin_project_root=normalized_project_root,
                    origin_session_id=normalized_session_id,
                    payload=normalized,
                    state="rejected",
                )

            created_at = datetime.now(timezone.utc).isoformat()
            record = CandidateRecord(
                id=candidate_id,
                kind=self.kind,
                created_at=created_at,
                storage_scope=normalized_scope,
                origin_project_root=normalized_project_root,
                origin_session_id=normalized_session_id,
                payload=normalized,
            )
            data = {
                "id": record.id,
                "kind": record.kind,
                "created_at": record.created_at,
                "storage_scope": record.storage_scope,
                "origin_project_root": record.origin_project_root,
                "origin_session_id": record.origin_session_id,
                "payload": record.payload,
            }
            serialized_size = len(_json_bytes(data))
            if serialized_size > MAX_CANDIDATE_FILE_BYTES:
                raise ValueError("candidate record exceeds size limit")
            count, queue_bytes = self._queue_usage()
            if count >= MAX_CANDIDATE_COUNT:
                raise ValueError("candidate queue count limit reached")
            if queue_bytes + serialized_size > MAX_CANDIDATE_QUEUE_BYTES:
                raise ValueError("candidate queue size limit reached")
            self._write_json(self._relative("pending", candidate_id), data, exclusive=True)
        logger.info("Staged %s candidate id=%s", self.kind, candidate_id)
        return record

    def _list_unlocked(self) -> list[CandidateRecord]:
        records: list[CandidateRecord] = []
        for directory, state in (("pending", "pending"), ("processing", "approving")):
            for name in list_trusted_names(self.root, directory):
                if not name.endswith(".json") or not _CANDIDATE_ID_RE.fullmatch(name[:-5]):
                    continue
                relative = Path(directory) / name
                try:
                    record = self._read(relative, state=state)
                    if (
                        self._approval_receipt_unlocked(record.id) is None
                        and self._rejection_receipt_unlocked(record.id) is None
                    ):
                        records.append(record)
                except Exception:
                    logger.warning("Skipping invalid %s candidate file", self.kind)
        return sorted(records, key=lambda record: (record.created_at, record.id))

    def list(self) -> list[CandidateRecord]:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._list_unlocked()

    def _get_unlocked(self, candidate_id: str) -> CandidateRecord | None:
        _validate_candidate_id(candidate_id)
        matches = []
        for directory, state in (("pending", "pending"), ("processing", "approving")):
            relative = self._relative(directory, candidate_id)
            if trusted_file_exists(self.root, relative):
                matches.append(self._read(relative, state=state))
        if len(matches) > 1:
            raise ValueError("candidate id exists in multiple states")
        return matches[0] if matches else None

    def get(self, candidate_id: str) -> CandidateRecord | None:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._get_unlocked(candidate_id)

    def _read_receipt(self, state: str, candidate_id: str) -> dict | None:
        relative = self._relative(state, candidate_id)
        if not trusted_file_exists(self.root, relative):
            return None
        data = self._read_json(relative, maximum_bytes=_RECEIPT_MAX_BYTES)
        expected = (
            {"id", "kind", "approved_at", "output_path"}
            if state == "approved"
            else {"id", "kind", "rejected_at"}
        )
        if set(data) != expected:
            raise ValueError(f"invalid {state} candidate receipt")
        if data.get("id") != candidate_id or data.get("kind") != self.kind:
            raise ValueError(f"invalid {state} candidate receipt identity")
        return data

    def _approval_receipt_unlocked(self, candidate_id: str) -> dict | None:
        return self._read_receipt("approved", candidate_id)

    def approval_receipt(self, candidate_id: str) -> dict | None:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._approval_receipt_unlocked(candidate_id)

    def _rejection_receipt_unlocked(self, candidate_id: str) -> dict | None:
        return self._read_receipt("rejected", candidate_id)

    def rejection_receipt(self, candidate_id: str) -> dict | None:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._rejection_receipt_unlocked(candidate_id)

    def _contains_id_unlocked(self, candidate_id: str) -> bool:
        return any(
            trusted_file_exists(self.root, self._relative(state, candidate_id))
            for state in ("pending", "processing", "approved", "rejected")
        )

    def _claim_unlocked(self, candidate_id: str) -> CandidateRecord | None:
        record = self._get_unlocked(candidate_id)
        if record is None:
            return None
        if self._approval_receipt_unlocked(candidate_id) is not None:
            return None
        if self._rejection_receipt_unlocked(candidate_id) is not None:
            return None
        if record.state == "approving":
            return self._read(self._relative("processing", candidate_id), state="approving")
        replace_trusted_file(
            self.root,
            self._relative("pending", candidate_id),
            self._relative("processing", candidate_id),
        )
        return self._read(self._relative("processing", candidate_id), state="approving")

    def claim(self, candidate_id: str) -> CandidateRecord | None:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._claim_unlocked(candidate_id)

    def _finish_approval_unlocked(self, record: CandidateRecord, output_path: Path) -> None:
        current = self._read(self._relative("processing", record.id), state="approving")
        if current != record:
            raise ValueError("processing candidate changed during approval")
        self._write_json(
            self._relative("approved", record.id),
            {
                "id": record.id,
                "kind": record.kind,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "output_path": str(output_path),
            },
            exclusive=True,
        )
        unlink_trusted_file(
            self.root,
            self._relative("processing", record.id),
            missing_ok=True,
        )

    def finish_approval(self, record: CandidateRecord, output_path: Path) -> None:
        with self.locked():
            self._finish_approval_unlocked(record, output_path)

    def _reject_unlocked(self, candidate_id: str) -> bool:
        if self._approval_receipt_unlocked(candidate_id) is not None:
            return False
        if self._rejection_receipt_unlocked(candidate_id) is not None:
            return True
        record = self._get_unlocked(candidate_id)
        if record is None:
            return False
        self._write_json(
            self._relative("rejected", candidate_id),
            {
                "id": candidate_id,
                "kind": self.kind,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
            },
            exclusive=True,
        )
        unlink_trusted_file(
            self.root,
            self._relative("pending" if record.state == "pending" else "processing", candidate_id),
            missing_ok=True,
        )
        logger.info("Rejected %s candidate id=%s", self.kind, candidate_id)
        return True

    def reject(self, candidate_id: str) -> bool:
        with self.locked():
            self._reconcile_receipted_unlocked()
            return self._reject_unlocked(candidate_id)


def default_memory_candidate_store() -> CandidateStore:
    return CandidateStore(Path.home() / ".koder" / "memory-candidates", kind="memory")


def default_skill_candidate_store() -> CandidateStore:
    return CandidateStore(Path.home() / ".koder" / "skill-candidates", kind="skill")


def _slugify(value: str, *, fallback: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug[:64] or fallback


def _write_exclusive_or_verify(
    root: Path,
    relative: Path,
    content: str | bytes,
) -> Path:
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        return write_approved_output(root, relative, data, exclusive=True)
    except FileExistsError:
        try:
            existing = read_trusted_file(
                root,
                relative,
                maximum_bytes=len(data),
                required_mode=0o600,
            )
        except Exception as exc:
            raise FileExistsError(f"approved output conflict: {relative}") from exc
        if existing != data:
            raise FileExistsError(f"approved output conflict: {relative}")
        return Path(os.path.abspath(os.path.expanduser(str(root)))) / relative


def _project_memory_dir(record: CandidateRecord) -> Path:
    project_root = Path(record.origin_project_root)
    if not project_root.is_dir() or Path(os.path.realpath(project_root)) != project_root:
        raise ValueError("candidate origin project root is unavailable")
    return project_root / ".koder" / "memory"


def _write_memory_candidate(record: CandidateRecord) -> Path:
    payload = validate_candidate_payload("memory", record.payload)
    content = render_memory_file(
        memory_type=payload["type"],
        description=payload["description"],
        body=payload["content"],
        metadata={
            "storage_scope": record.storage_scope,
            "source_candidate": record.id,
        },
    )
    memory_dir = (
        _project_memory_dir(record)
        if record.storage_scope == "project"
        else Path.home() / ".koder" / "memory"
    )
    return _write_exclusive_or_verify(
        memory_dir,
        Path(f"auto-dream-{record.id}.md"),
        content,
    )


def _write_skill_draft(record: CandidateRecord, skill_draft_dir: Path) -> Path:
    payload = validate_candidate_payload("skill", record.payload)
    slug = _slugify(payload["name"], fallback="skill-candidate")
    draft_name = f"draft-{slug}-{record.id}"
    frontmatter = yaml.safe_dump(
        {
            "name": draft_name,
            "description": payload["description"],
            "user-invocable": False,
            "disable-model-invocation": True,
            "allowed-tools": [],
            "status": "draft",
            "source-candidate": record.id,
            "storage-scope": record.storage_scope,
            "origin-project-root": record.origin_project_root,
            "origin-session-id": record.origin_session_id,
        },
        sort_keys=False,
    ).strip()
    return _write_exclusive_or_verify(
        skill_draft_dir,
        Path(f"{slug}-{record.id}") / "SKILL.md",
        f"---\n{frontmatter}\n---\n{payload['instructions']}\n",
    )


@contextmanager
def _locked_stores(stores: list[CandidateStore | None]) -> Iterator[list[CandidateStore]]:
    unique: dict[tuple[str, CandidateKind], CandidateStore] = {}
    for store in stores:
        if store is not None:
            unique[(str(store.root), store.kind)] = store
    ordered = [unique[key] for key in sorted(unique)]
    with ExitStack() as stack:
        for store in ordered:
            stack.enter_context(store.locked())
        for store in ordered:
            store._reconcile_receipted_unlocked()
        yield ordered


def _candidate_store_for_id_unlocked(
    candidate_id: str,
    *,
    memory_store: CandidateStore | None,
    skill_store: CandidateStore | None,
) -> CandidateStore | None:
    _validate_candidate_id(candidate_id)
    matches = [
        store
        for store in (memory_store, skill_store)
        if store is not None and store._contains_id_unlocked(candidate_id)
    ]
    if len(matches) > 1:
        raise ValueError("duplicate candidate id across memory and skill stores")
    return matches[0] if matches else None


def approve_candidate(
    candidate_id: str,
    *,
    memory_store: CandidateStore | None,
    skill_store: CandidateStore | None,
    skill_draft_dir: Path | None = None,
) -> CandidateApprovalResult:
    with _locked_stores([memory_store, skill_store]):
        store = _candidate_store_for_id_unlocked(
            candidate_id,
            memory_store=memory_store,
            skill_store=skill_store,
        )
        if store is None:
            raise KeyError(candidate_id)
        receipt = store._approval_receipt_unlocked(candidate_id)
        if receipt is not None:
            output = receipt.get("output_path")
            return CandidateApprovalResult(
                candidate_id=candidate_id,
                kind=store.kind,
                status="already-approved",
                output_path=Path(output) if output else None,
            )
        if store._rejection_receipt_unlocked(candidate_id) is not None:
            raise ValueError("candidate was rejected")

        record = store._claim_unlocked(candidate_id)
        if record is None:
            raise KeyError(candidate_id)
        target_skill_draft_dir = skill_draft_dir or Path.home() / ".koder" / "skill-drafts"
        output_path = (
            _write_memory_candidate(record)
            if record.kind == "memory"
            else _write_skill_draft(record, target_skill_draft_dir)
        )
        store._finish_approval_unlocked(record, output_path)
        logger.info("Approved %s candidate id=%s", record.kind, record.id)
        return CandidateApprovalResult(
            candidate_id=record.id,
            kind=record.kind,
            status="approved",
            output_path=output_path,
        )


def reject_candidate(
    candidate_id: str,
    *,
    memory_store: CandidateStore | None,
    skill_store: CandidateStore | None,
) -> bool:
    with _locked_stores([memory_store, skill_store]):
        store = _candidate_store_for_id_unlocked(
            candidate_id,
            memory_store=memory_store,
            skill_store=skill_store,
        )
        if store is None:
            return False
        return store._reject_unlocked(candidate_id)
