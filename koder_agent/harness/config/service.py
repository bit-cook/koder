"""Runtime config loading and saving."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

from koder_agent.config.manager import _migrate_legacy_voice_fields
from koder_agent.harness.hooks.runtime import dispatch_command_hooks

from .schema import RuntimeConfig


class RuntimeConfigService:
    """Loads and saves the runtime config schema at the existing path."""

    DEFAULT_CONFIG_PATH = Path.home() / ".koder" / "config.yaml"

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (Path.home() / ".koder" / "config.yaml")
        self._config: RuntimeConfig | None = None

    def load(self) -> RuntimeConfig:
        if self._config is not None:
            return self._config
        if self.config_path.exists():
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            data = _migrate_legacy_voice_fields(data)
            self._config = RuntimeConfig(**data)
        else:
            self._config = RuntimeConfig()
        return self._config

    def save(self, config: RuntimeConfig | None = None) -> None:
        config = config or self._config or RuntimeConfig()
        previous_text = (
            self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else None
        )
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.safe_dump(
                config.model_dump(exclude_none=False),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        self._config = config
        source = (
            "user_settings"
            if self.config_path.resolve().is_relative_to(Path.home().resolve())
            else "project_settings"
        )
        result = dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name="ConfigChange",
            match_value=source,
            payload={
                "event": "ConfigChange",
                "source": source,
                "file_path": str(self.config_path.resolve()),
            },
        )
        if result.blocked:
            if previous_text is None:
                self.config_path.unlink(missing_ok=True)
            else:
                self.config_path.write_text(previous_text, encoding="utf-8")
            raise RuntimeError(result.block_reason or "Config change blocked by hook")

    def get_effective_value(
        self,
        config_value: Any,
        env_var_name: Optional[str],
        cli_value: Any = None,
    ) -> Any:
        if cli_value is not None:
            return cli_value
        if env_var_name:
            env_value = os.environ.get(env_var_name)
            if env_value is not None:
                return env_value
        return config_value
