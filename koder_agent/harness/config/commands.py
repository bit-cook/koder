"""Harness config subcommand handlers."""

from __future__ import annotations

import argparse
import subprocess

import yaml

from koder_agent.config import get_config_manager
from koder_agent.harness.config.schema import RuntimeConfig
from koder_agent.harness.config.settings_bundle import (
    export_settings_bundle,
    import_settings_bundle,
)


async def handle_config_subcommand(args: argparse.Namespace) -> int:
    manager = get_config_manager()

    if args.config_action in {"show", "list"}:
        data = manager.load().model_dump(exclude_none=False)
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

    print("Usage: koder config <show|list|path|edit|init|set|export|import>")
    return 0
