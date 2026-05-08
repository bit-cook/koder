"""MagicDocs: self-maintained project notes for Koder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAGIC_DOC_HEADER = "# MAGIC DOC:"
MANAGED_SECTION_HEADING = "## Koder Session Notes"
MANAGED_SECTION_START = "<!-- koder-magic-docs:auto-refresh-start -->"
MANAGED_SECTION_END = "<!-- koder-magic-docs:auto-refresh-end -->"

_HEADER_RE = re.compile(r"^#\s*MAGIC\s+DOC:\s*(.+?)\s*$", re.IGNORECASE)
_ITALICS_RE = re.compile(r"^[_*](.+?)[_*]\s*$")
_MANAGED_SECTION_RE = re.compile(
    rf"\n*{re.escape(MANAGED_SECTION_HEADING)}\n\n"
    rf"{re.escape(MANAGED_SECTION_START)}.*?{re.escape(MANAGED_SECTION_END)}\n*",
    re.DOTALL,
)


@dataclass
class MagicDoc:
    """A documentation file marked for Koder maintenance."""

    path: Path
    title: str
    content: str
    last_updated: str
    instructions: str | None = None


@dataclass
class TrackedMagicDoc:
    """Runtime tracking state for a Magic Doc read by Koder."""

    path: Path
    title: str
    instructions: str | None
    registered_at: str
    last_seen: str
    refresh_count: int = 0
    last_refresh: str | None = None
    last_result: str = "registered"


@dataclass(frozen=True)
class MagicDocRefreshPlanItem:
    """Deterministic refresh plan entry."""

    path: Path
    title: str
    instructions: str | None
    tracked: bool
    reason: str


@dataclass(frozen=True)
class MagicDocRefreshResult:
    """Result from a Magic Doc refresh attempt."""

    path: Path
    title: str
    status: str
    changed: bool
    message: str


_tracked_magic_docs: dict[Path, TrackedMagicDoc] = {}


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _date(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _display_path(path: Path, cwd: Path | None = None) -> str:
    base = (cwd or Path.cwd()).resolve()
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


def _compact_line(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def detect_magic_doc_header(content: str) -> tuple[str, str | None] | None:
    """Return the Magic Doc title and optional italic instruction line."""

    lines = content.splitlines()
    if not lines:
        return None

    match = _HEADER_RE.match(lines[0])
    if not match:
        return None

    title = match.group(1).strip()
    instructions = None
    next_index = 1
    if next_index < len(lines) and not lines[next_index].strip():
        next_index += 1
    if next_index < len(lines):
        italics_match = _ITALICS_RE.match(lines[next_index].strip())
        if italics_match:
            instructions = italics_match.group(1).strip()
    return title, instructions


def is_magic_doc(path: Path) -> bool:
    """Check if a file starts with the Magic Doc header."""

    if not path.exists():
        return False
    try:
        return detect_magic_doc_header(path.read_text(encoding="utf-8")) is not None
    except (OSError, UnicodeDecodeError):
        return False


def find_magic_docs(directory: Path) -> list[MagicDoc]:
    """Find Magic Docs under a directory recursively."""

    magic_docs: list[MagicDoc] = []
    for md_file in sorted(directory.rglob("*.md")):
        if is_magic_doc(md_file):
            magic_docs.append(_load_magic_doc(md_file))
    return magic_docs


def create_magic_doc(path: Path, title: str, initial_content: str) -> MagicDoc:
    """Create a new Magic Doc with the required header."""

    timestamp = _date()
    full_content = f"{MAGIC_DOC_HEADER} {title}\n\n{initial_content}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(full_content, encoding="utf-8")
    return MagicDoc(path=path, title=title, content=initial_content, last_updated=timestamp)


def update_magic_doc(path: Path, new_content: str) -> None:
    """Update a Magic Doc while preserving its header and instruction line."""

    magic_doc = _load_magic_doc(path)
    if not magic_doc.title:
        raise ValueError(f"{path} is not a magic doc")

    lines = path.read_text(encoding="utf-8").splitlines()
    preserved = [lines[0]]
    next_index = 1
    if next_index < len(lines) and not lines[next_index].strip():
        preserved.append("")
        next_index += 1
    if next_index < len(lines) and _ITALICS_RE.match(lines[next_index].strip()):
        preserved.append(lines[next_index])

    path.write_text("\n".join(preserved).rstrip() + f"\n\n{new_content}", encoding="utf-8")


def clear_tracked_magic_docs() -> None:
    """Clear runtime Magic Doc tracking state."""

    _tracked_magic_docs.clear()


def register_magic_doc(path: Path, content: str | None = None) -> MagicDoc | None:
    """Track a Magic Doc after Koder reads it."""

    resolved = path.resolve()
    try:
        raw_content = content if content is not None else resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _tracked_magic_docs.pop(resolved, None)
        return None

    detected = detect_magic_doc_header(raw_content)
    if detected is None:
        _tracked_magic_docs.pop(resolved, None)
        return None

    title, instructions = detected
    now = _timestamp()
    existing = _tracked_magic_docs.get(resolved)
    if existing is None:
        _tracked_magic_docs[resolved] = TrackedMagicDoc(
            path=resolved,
            title=title,
            instructions=instructions,
            registered_at=now,
            last_seen=now,
        )
    else:
        existing.title = title
        existing.instructions = instructions
        existing.last_seen = now
        existing.last_result = "registered"

    loaded = _load_magic_doc_from_content(resolved, raw_content)
    return loaded


def get_tracked_magic_docs() -> list[TrackedMagicDoc]:
    """Return tracked Magic Docs in path order."""

    return [record for _, record in sorted(_tracked_magic_docs.items())]


def build_magic_doc_refresh_plan(directory: Path | None = None) -> list[MagicDocRefreshPlanItem]:
    """Build a deterministic plan from tracked docs and discovered marker files."""

    plan: dict[Path, MagicDocRefreshPlanItem] = {}
    for record in get_tracked_magic_docs():
        plan[record.path] = MagicDocRefreshPlanItem(
            path=record.path,
            title=record.title,
            instructions=record.instructions,
            tracked=True,
            reason="read by Koder in this runtime",
        )

    if directory is not None and directory.exists():
        for doc in find_magic_docs(directory):
            resolved = doc.path.resolve()
            plan.setdefault(
                resolved,
                MagicDocRefreshPlanItem(
                    path=resolved,
                    title=doc.title,
                    instructions=doc.instructions,
                    tracked=False,
                    reason="discovered in workspace",
                ),
            )

    return [plan[path] for path in sorted(plan)]


def format_magic_docs_status(directory: Path | None = None) -> str:
    """Render Magic Doc status for the TUI."""

    plan = build_magic_doc_refresh_plan(directory)
    tracked_count = sum(1 for item in plan if item.tracked)
    lines = [
        "magic_docs:",
        f"  discovered: {len(plan)}",
        f"  tracked: {tracked_count}",
        "  auto_refresh: enabled after Koder reads a Magic Doc",
    ]
    if not plan:
        lines.append("  docs: none")
        return "\n".join(lines)

    lines.append("  docs:")
    tracked_lookup = {record.path: record for record in get_tracked_magic_docs()}
    for item in plan:
        record = tracked_lookup.get(item.path)
        suffix = "tracked" if item.tracked else "discovered"
        if record and record.last_refresh:
            suffix = f"{suffix}, refreshed {record.last_refresh}"
        lines.append(f"    - {_display_path(item.path, directory)}: {item.title} ({suffix})")
    return "\n".join(lines)


def refresh_tracked_magic_docs(
    user_input: str,
    assistant_output: str,
    *,
    cwd: Path | None = None,
    include_untracked: bool = False,
    now: datetime | None = None,
) -> list[MagicDocRefreshResult]:
    """Refresh tracked Magic Docs with a deterministic local session note."""

    if include_untracked and cwd is not None:
        for doc in find_magic_docs(cwd):
            register_magic_doc(doc.path)

    results: list[MagicDocRefreshResult] = []
    for record in list(get_tracked_magic_docs()):
        results.append(_refresh_one_magic_doc(record, user_input, assistant_output, now=now))
    return results


def _refresh_one_magic_doc(
    record: TrackedMagicDoc,
    user_input: str,
    assistant_output: str,
    *,
    now: datetime | None,
) -> MagicDocRefreshResult:
    path = record.path
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _tracked_magic_docs.pop(path, None)
        return MagicDocRefreshResult(path, record.title, "removed", False, "file unavailable")

    detected = detect_magic_doc_header(current)
    if detected is None:
        _tracked_magic_docs.pop(path, None)
        return MagicDocRefreshResult(path, record.title, "removed", False, "header missing")

    title, instructions = detected
    record.title = title
    record.instructions = instructions
    note = _build_refresh_note(user_input, assistant_output, now=now)
    if note is None:
        record.last_result = "skipped"
        return MagicDocRefreshResult(path, title, "skipped", False, "no substantial turn text")

    updated = _replace_managed_section(current, note)
    if updated == current:
        record.last_result = "unchanged"
        return MagicDocRefreshResult(path, title, "unchanged", False, "managed section current")

    path.write_text(updated, encoding="utf-8")
    stamp = _timestamp(now)
    record.refresh_count += 1
    record.last_refresh = stamp
    record.last_seen = stamp
    record.last_result = "updated"
    return MagicDocRefreshResult(path, title, "updated", True, "managed section refreshed")


def _build_refresh_note(
    user_input: str,
    assistant_output: str,
    *,
    now: datetime | None,
) -> str | None:
    user_preview = _compact_line(user_input, limit=240)
    assistant_preview = _compact_line(assistant_output, limit=360)
    if len(user_preview) < 12 and len(assistant_preview) < 12:
        return None

    lines = [
        MANAGED_SECTION_HEADING,
        "",
        MANAGED_SECTION_START,
        f"Last refreshed: {_timestamp(now)}",
        "",
    ]
    if user_preview:
        lines.append(f"- User: {user_preview}")
    if assistant_preview:
        lines.append(f"- Koder: {assistant_preview}")
    lines.append(MANAGED_SECTION_END)
    return "\n".join(lines).rstrip() + "\n"


def _replace_managed_section(content: str, managed_section: str) -> str:
    if MANAGED_SECTION_START in content and MANAGED_SECTION_END in content:
        return _MANAGED_SECTION_RE.sub("\n\n" + managed_section, content, count=1).rstrip() + "\n"
    return content.rstrip() + "\n\n" + managed_section


def _load_magic_doc(path: Path) -> MagicDoc:
    return _load_magic_doc_from_content(path, path.read_text(encoding="utf-8"))


def _load_magic_doc_from_content(path: Path, content: str) -> MagicDoc:
    detected = detect_magic_doc_header(content)
    if detected is None:
        raise ValueError(f"{path} is not a magic doc")

    title, instructions = detected
    lines = content.split("\n", 1)
    doc_content = lines[1].lstrip("\n") if len(lines) > 1 else ""
    timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    return MagicDoc(
        path=path,
        title=title,
        content=doc_content,
        last_updated=timestamp,
        instructions=instructions,
    )
