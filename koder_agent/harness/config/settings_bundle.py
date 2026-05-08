"""Local settings bundle import and export."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from .schema import RuntimeConfig

SettingsBundleScope = Literal["all", "user", "project"]

BUNDLE_FORMAT = "koder-settings-bundle"
BUNDLE_VERSION = 1
MAX_BUNDLE_FILE_BYTES = 500 * 1024


@dataclass(frozen=True)
class SettingsBundleExportResult:
    """Summary of a settings bundle export."""

    bundle_path: Path
    file_count: int
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SettingsBundleImportResult:
    """Summary of a settings bundle import."""

    bundle_path: Path
    written: int
    unchanged: int
    backups: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False


def export_settings_bundle(
    bundle_path: str | Path,
    *,
    scope: SettingsBundleScope = "all",
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> SettingsBundleExportResult:
    """Export local Koder settings and memory files into a JSON bundle."""
    if scope not in {"all", "user", "project"}:
        raise ValueError("scope must be one of: all, user, project")

    home_dir = Path(home).expanduser() if home is not None else Path.home()
    cwd_dir = Path(cwd).resolve() if cwd is not None else Path.cwd()
    target = Path(bundle_path).expanduser()
    files: list[dict] = []
    skipped: list[str] = []

    for role, file_scope, path in _known_direct_files(home_dir, cwd_dir):
        if not _scope_included(file_scope, scope):
            continue
        _append_file_entry(
            files,
            skipped,
            role=role,
            file_scope=file_scope,
            path=path,
            relative_path=path.name,
        )

    for role, file_scope, base in _known_directory_files(home_dir, cwd_dir):
        if not _scope_included(file_scope, scope) or not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            try:
                relative_path = path.relative_to(base).as_posix()
            except ValueError:
                skipped.append(f"{path}: outside base directory")
                continue
            _append_file_entry(
                files,
                skipped,
                role=role,
                file_scope=file_scope,
                path=path,
                relative_path=relative_path,
            )

    payload = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "files": files,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return SettingsBundleExportResult(bundle_path=target, file_count=len(files), skipped=skipped)


def import_settings_bundle(
    bundle_path: str | Path,
    *,
    scope: SettingsBundleScope = "all",
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    dry_run: bool = False,
) -> SettingsBundleImportResult:
    """Import a JSON settings bundle into the current Koder home and project."""
    if scope not in {"all", "user", "project"}:
        raise ValueError("scope must be one of: all, user, project")

    source = Path(bundle_path).expanduser()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("format") != BUNDLE_FORMAT or payload.get("version") != BUNDLE_VERSION:
        raise ValueError("Unsupported Koder settings bundle format")

    home_dir = Path(home).expanduser() if home is not None else Path.home()
    cwd_dir = Path(cwd).resolve() if cwd is not None else Path.cwd()
    written = 0
    unchanged = 0
    backups: list[Path] = []
    skipped: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    for entry in payload.get("files", []):
        role = entry.get("role")
        file_scope = entry.get("scope")
        if file_scope not in {"user", "project"} or not _scope_included(file_scope, scope):
            continue
        content = entry.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("Invalid settings bundle file entry")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError(f"Checksum mismatch for bundle entry {role}")
        _validate_content(role, content)
        target = _target_path_for_entry(role, entry.get("relative_path"), home_dir, cwd_dir)
        if target is None:
            raise ValueError(f"Unknown settings bundle role: {role}")
        if target.is_symlink():
            raise ValueError(f"Refusing to import over symlink target: {target}")
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing == content:
            unchanged += 1
            continue
        if dry_run:
            written += 1
            continue
        if target.exists():
            backup = _backup_path(target, stamp)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(existing or "", encoding="utf-8")
            backups.append(backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written += 1

    return SettingsBundleImportResult(
        bundle_path=source,
        written=written,
        unchanged=unchanged,
        backups=backups,
        skipped=skipped,
        dry_run=dry_run,
    )


def _known_direct_files(home_dir: Path, cwd_dir: Path) -> list[tuple[str, str, Path]]:
    return [
        ("user_config", "user", home_dir / ".koder" / "config.yaml"),
        ("user_settings", "user", home_dir / ".koder" / "settings.json"),
        ("user_keybindings", "user", home_dir / ".koder" / "keybindings.json"),
        ("project_settings", "project", cwd_dir / ".koder" / "settings.json"),
        ("project_local_settings", "project", cwd_dir / ".koder" / "settings.local.json"),
    ]


def _known_directory_files(home_dir: Path, cwd_dir: Path) -> list[tuple[str, str, Path]]:
    return [
        ("user_memory", "user", home_dir / ".koder" / "memory"),
        ("project_memory", "project", cwd_dir / ".koder" / "memory"),
        ("project_session_memory", "project", cwd_dir / ".koder" / "session-memory"),
    ]


def _scope_included(file_scope: str, requested_scope: SettingsBundleScope) -> bool:
    return requested_scope == "all" or requested_scope == file_scope


def _append_file_entry(
    files: list[dict],
    skipped: list[str],
    *,
    role: str,
    file_scope: str,
    path: Path,
    relative_path: str,
) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        skipped.append(f"{path}: symlink skipped")
        return
    if path.stat().st_size > MAX_BUNDLE_FILE_BYTES:
        skipped.append(f"{path}: exceeds {MAX_BUNDLE_FILE_BYTES} bytes")
        return
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        skipped.append(f"{path}: not UTF-8 text")
        return
    files.append(
        {
            "role": role,
            "scope": file_scope,
            "relative_path": relative_path,
            "size": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }
    )


def _target_path_for_entry(
    role: str,
    relative_path: object,
    home_dir: Path,
    cwd_dir: Path,
) -> Path | None:
    direct_targets = {
        "user_config": home_dir / ".koder" / "config.yaml",
        "user_settings": home_dir / ".koder" / "settings.json",
        "user_keybindings": home_dir / ".koder" / "keybindings.json",
        "project_settings": cwd_dir / ".koder" / "settings.json",
        "project_local_settings": cwd_dir / ".koder" / "settings.local.json",
    }
    if role in direct_targets:
        return direct_targets[role]

    directory_targets = {
        "user_memory": home_dir / ".koder" / "memory",
        "project_memory": cwd_dir / ".koder" / "memory",
        "project_session_memory": cwd_dir / ".koder" / "session-memory",
    }
    if role not in directory_targets or not isinstance(relative_path, str):
        return None
    safe_relative = _safe_relative_path(relative_path)
    return directory_targets[role] / safe_relative


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe bundle relative path: {value}")
    return path


def _validate_content(role: str, content: str) -> None:
    if role == "user_config":
        RuntimeConfig(**(yaml.safe_load(content) or {}))
        return
    if role.endswith("settings") or role == "user_keybindings":
        json.loads(content or "{}")


def _backup_path(target: Path, stamp: str) -> Path:
    candidate = target.with_name(f"{target.name}.bak-{stamp}")
    index = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.bak-{stamp}-{index}")
        index += 1
    return candidate
