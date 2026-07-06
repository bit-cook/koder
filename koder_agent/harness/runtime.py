"""Minimal harness runtime shell."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from koder_agent.harness.bootstrap import build_registries
from koder_agent.harness.config.service import RuntimeConfigService
from koder_agent.harness.paths import harness_home_dir
from koder_agent.harness.permissions.ai_classifier import AiShellClassifier
from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.rule_sources import RuleHierarchy
from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.session_flow import run_harness_session_flow
from koder_agent.harness.version_info import render_cli_version_banner


def _load_permission_hierarchy() -> RuleHierarchy:
    """Load permission rules from project, local, and user settings files."""
    hierarchy = RuleHierarchy()

    # Load project settings (.koder/settings.json)
    project_settings_path = Path.cwd() / ".koder" / "settings.json"
    if project_settings_path.exists():
        try:
            settings = json.loads(project_settings_path.read_text(encoding="utf-8"))
            hierarchy.load_from_settings(settings, source="project")
        except (json.JSONDecodeError, OSError):
            pass  # Ignore malformed or unreadable files

    # Load local project settings (.koder/settings.local.json, gitignored)
    local_settings_path = Path.cwd() / ".koder" / "settings.local.json"
    if local_settings_path.exists():
        try:
            settings = json.loads(local_settings_path.read_text(encoding="utf-8"))
            hierarchy.load_from_settings(settings, source="local")
        except (json.JSONDecodeError, OSError):
            pass  # Ignore malformed or unreadable files

    # Load user settings (~/.koder/settings.json)
    user_settings_path = harness_home_dir() / "settings.json"
    if user_settings_path.exists():
        try:
            settings = json.loads(user_settings_path.read_text(encoding="utf-8"))
            hierarchy.load_from_settings(settings, source="user")
        except (json.JSONDecodeError, OSError):
            pass  # Ignore malformed or unreadable files

    return hierarchy


@dataclass
class HarnessRuntime:
    request: object

    async def run(self) -> int:
        # Create permission hierarchy and AI classifier
        rule_hierarchy = _load_permission_hierarchy()
        ai_classifier = AiShellClassifier()

        # Resolve effective permission mode: CLI > ENV > Config > Default
        config_service = RuntimeConfigService()
        config = config_service.load()
        cli_permission_mode = getattr(self.request, "permission_mode", None)
        effective_mode_str = config_service.get_effective_value(
            config.harness.permission_mode,
            "KODER_PERMISSION_MODE",
            cli_permission_mode,
        )
        try:
            effective_mode = PermissionMode(effective_mode_str)
        except ValueError:
            effective_mode = PermissionMode.DEFAULT

        permission_service = PermissionService.default(
            mode=effective_mode,
            rule_hierarchy=rule_hierarchy,
            ai_classifier=ai_classifier,
        )
        command_registry, tool_registry = build_registries(permission_service=permission_service)
        mode = getattr(self.request, "mode", "")
        argv = list(getattr(self.request, "argv", []))

        if mode == "help":
            help_text = getattr(self.request, "help_text", None)
            if help_text:
                sys.stdout.write(help_text)
                return 0
            console = Console()
            console.print("koder harness runtime bootstrap")
            console.print(f"commands: {len(command_registry.list_names())}")
            console.print(f"tools: {len(tool_registry.list_names())}")
            return 0

        if mode == "version":
            sys.stdout.write(render_cli_version_banner() + "\n")
            return 0

        if mode == "interactive":
            return await run_harness_session_flow(
                first_arg=None,
                argv=argv,
                permission_service=permission_service,
            )

        if mode in {"prompt", "subcommand", "auth_passthrough"}:
            first_arg = getattr(self.request, "first_arg", None)
            return await run_harness_session_flow(
                first_arg=first_arg,
                argv=argv,
                permission_service=permission_service,
            )
        return 0
