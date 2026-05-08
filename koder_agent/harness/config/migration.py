"""Backup-first runtime config migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .backup import create_config_backup
from .schema import RuntimeConfig


@dataclass(frozen=True)
class ConfigMigrationResult:
    """Outcome of a config migration run."""

    config_path: Path
    backup_path: Path


def migrate_config_file(
    config_path: str | Path,
    *,
    legacy_db_path: str | Path | None = None,
) -> ConfigMigrationResult:
    """Rewrite the config file into the runtime schema after creating a backup."""
    path = Path(config_path)
    backup_path = create_config_backup(path)

    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    runtime_config = RuntimeConfig(**data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            runtime_config.model_dump(exclude_none=False),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # Explicitly do nothing to the legacy DB beyond accepting the path.
    if legacy_db_path is not None:
        _ = Path(legacy_db_path)

    return ConfigMigrationResult(config_path=path, backup_path=backup_path)
