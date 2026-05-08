"""Backup helpers for reversible config migrations."""

from __future__ import annotations

from pathlib import Path


def create_config_backup(config_path: Path) -> Path:
    """Create a sibling backup for a config file before rewriting it."""
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if config_path.exists():
        backup_path.write_bytes(config_path.read_bytes())
    return backup_path
