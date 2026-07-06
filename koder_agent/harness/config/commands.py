"""Harness config subcommand handlers."""

from __future__ import annotations

import argparse
import subprocess

import yaml
from pydantic import ValidationError

from koder_agent.config import get_config_manager
from koder_agent.harness.config.schema import RuntimeConfig
from koder_agent.harness.config.service import RuntimeConfigService
from koder_agent.harness.config.settings_bundle import (
    export_settings_bundle,
    import_settings_bundle,
)

# Known config keys that can be overridden by environment variables.
# Maps a dotted config path to the environment variable that overlays it.
_EFFECTIVE_ENV_KEYS: dict[str, str] = {
    "model.name": "KODER_MODEL",
    "model.api_key": "KODER_API_KEY",
    "model.base_url": "KODER_BASE_URL",
    "model.reasoning_effort": "KODER_REASONING_EFFORT",
}


def _set_nested(data: dict, dotted_key: str, value: object) -> None:
    keys = dotted_key.split(".")
    current = data
    for key in keys[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            return
        current = node
    if keys[-1] in current:
        current[keys[-1]] = value


def _get_nested(data: dict, dotted_key: str) -> object:
    keys = dotted_key.split(".")
    current: object = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


async def handle_config_subcommand(args: argparse.Namespace) -> int:
    manager = get_config_manager()

    if args.config_action == "validate":
        return _handle_config_validate()

    if args.config_action in {"show", "list"}:
        data = manager.load().model_dump(exclude_none=False)
        if args.config_action == "show" and getattr(args, "effective", False):
            service = RuntimeConfigService(config_path=manager.config_path)
            for dotted_key, env_var in _EFFECTIVE_ENV_KEYS.items():
                effective = service.get_effective_value(_get_nested(data, dotted_key), env_var)
                _set_nested(data, dotted_key, effective)
        print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip())
        return 0

    if args.config_action == "path":
        print(manager.config_path)
        return 0

    if args.config_action == "init":
        if manager.config_path.exists():
            print(f"Config file already exists at {manager.config_path}")
            return 1
        manager.save(RuntimeConfig())
        print(f"Created config file at {manager.config_path}")
        return 0

    if args.config_action == "edit":
        if not manager.config_path.exists():
            manager.save(RuntimeConfig())
        editor = (
            subprocess.list2cmdline([arg]) for arg in []
        )  # pragma: no cover - quiet lint placeholder
        editor = None
        import os
        import sys

        editor = os.environ.get("EDITOR")
        if not editor:
            if sys.platform == "win32":
                editor = "notepad"
            elif sys.platform == "darwin":
                editor = "open -e"
            else:
                editor = "nano"
        try:
            subprocess.run([editor, str(manager.config_path)], check=True)
        except FileNotFoundError:
            subprocess.run(f"{editor} {manager.config_path}", shell=True, check=True)
        return 0

    if args.config_action == "export":
        try:
            result = export_settings_bundle(args.path, scope=args.scope)
        except (OSError, ValueError) as exc:
            print(f"Config export failed: {exc}")
            return 1
        print(f"Exported settings bundle to {result.bundle_path}")
        print(f"files: {result.file_count}")
        if result.skipped:
            print(f"skipped: {len(result.skipped)}")
            for item in result.skipped:
                print(f"- {item}")
        return 0

    if args.config_action == "import":
        try:
            result = import_settings_bundle(
                args.path,
                scope=args.scope,
                dry_run=getattr(args, "dry_run", False),
            )
        except (OSError, ValueError) as exc:
            print(f"Config import failed: {exc}")
            return 1
        verb = "Checked" if result.dry_run else "Imported"
        print(f"{verb} settings bundle from {result.bundle_path}")
        write_label = "would_write" if result.dry_run else "written"
        print(f"{write_label}: {result.written}")
        print(f"unchanged: {result.unchanged}")
        print(f"backups: {len(result.backups)}")
        if result.skipped:
            print(f"skipped: {len(result.skipped)}")
            for item in result.skipped:
                print(f"- {item}")
        return 0

    if args.config_action == "set":
        config = manager.load()
        data = config.model_dump(exclude_none=False)
        current = data
        keys = args.key.split(".")
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        value = args.value
        lowered = value.lower()
        if lowered == "true":
            parsed = True
        elif lowered == "false":
            parsed = False
        elif lowered in {"null", "none"}:
            parsed = None
        else:
            try:
                parsed = int(value)
            except ValueError:
                try:
                    parsed = float(value)
                except ValueError:
                    parsed = value
        current[keys[-1]] = parsed
        manager.save(RuntimeConfig(**data))
        print(f"Set {args.key} = {args.value}")
        return 0

    print("Usage: koder config <show|list|path|edit|init|set|validate|export|import>")
    return 0


def _handle_config_validate() -> int:
    """Load the config YAML and validate it against the RuntimeConfig schema.

    Returns 0 when the config is valid (or absent, since defaults apply) and a
    non-zero exit code with a rendered pydantic ValidationError otherwise.
    """
    service = RuntimeConfigService()
    config_path = service.config_path

    if not config_path.exists():
        print(f"Config file not found at {config_path}; defaults are valid.")
        return 0

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"Config invalid: YAML parse error at {config_path}: {exc}")
        return 1

    if not isinstance(raw, dict):
        print(f"Config invalid: expected a mapping at {config_path}, got {type(raw).__name__}.")
        return 1

    try:
        RuntimeConfig(**raw)
    except ValidationError as exc:
        print(f"Config invalid: {config_path}")
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ())) or "(root)"
            message = error.get("msg", "invalid value")
            print(f"- {location}: {message}")
        return 1

    print(f"Config valid: {config_path}")
    return 0
