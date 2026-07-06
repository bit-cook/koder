"""Shared runtime for koder command hooks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from koder_agent.harness.managed_settings import load_managed_settings, managed_settings_path
from koder_agent.harness.paths import harness_home_dir, settings_path
from koder_agent.harness.session_env import (
    apply_session_env_file_to_process,
    session_env_file,
)

logger = logging.getLogger(__name__)

# Events that receive KODER_ENV_FILE.
_ENV_FILE_EVENTS = frozenset({"SessionStart", "CwdChanged", "FileChanged"})

# ---------------------------------------------------------------------------
# Hook event types & typed payloads
# ---------------------------------------------------------------------------

# Complete set of hook events.
HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "PermissionRequest",
    "PermissionDenied",
    "Setup",
    "ConfigChange",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "WorktreeCreate",
    "WorktreeRemove",
    "CwdChanged",
    "InstructionsLoaded",
    "FileChanged",
    "Elicitation",
    "ElicitationResult",
}


@dataclass(frozen=True)
class HookPayload:
    """Typed payload for hook dispatch."""

    event: str
    data: dict[str, Any]

    @staticmethod
    def pre_tool_use(tool_name: str, tool_input: dict) -> "HookPayload":
        return HookPayload("PreToolUse", {"tool_name": tool_name, "tool_input": tool_input})

    @staticmethod
    def post_tool_use(tool_name: str, tool_input: dict, tool_output: str) -> "HookPayload":
        return HookPayload(
            "PostToolUse",
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_output,
            },
        )

    @staticmethod
    def post_tool_use_failure(tool_name: str, tool_input: dict, error: str) -> "HookPayload":
        return HookPayload(
            "PostToolUseFailure",
            {"tool_name": tool_name, "tool_input": tool_input, "error": error},
        )

    @staticmethod
    def user_prompt_submit(prompt: str, session_id: str = "") -> "HookPayload":
        return HookPayload("UserPromptSubmit", {"prompt": prompt, "session_id": session_id})

    @staticmethod
    def pre_compact(token_count: int, threshold: int) -> "HookPayload":
        return HookPayload("PreCompact", {"token_count": token_count, "threshold": threshold})

    @staticmethod
    def post_compact(old_count: int = 0, new_count: int = 0) -> "HookPayload":
        return HookPayload(
            "PostCompact",
            {"old_message_count": old_count, "new_message_count": new_count},
        )

    @staticmethod
    def permission_denied(tool_name: str, tool_input: dict, reason: str) -> "HookPayload":
        return HookPayload(
            "PermissionDenied",
            {"tool_name": tool_name, "tool_input": tool_input, "reason": reason},
        )

    @staticmethod
    def permission_request(tool_name: str, tool_input: dict) -> "HookPayload":
        return HookPayload(
            "PermissionRequest",
            {"tool_name": tool_name, "tool_input": tool_input},
        )

    @staticmethod
    def cwd_changed(old_cwd: str, new_cwd: str) -> "HookPayload":
        return HookPayload("CwdChanged", {"old_cwd": old_cwd, "new_cwd": new_cwd})

    @staticmethod
    def instructions_loaded(paths: list[str]) -> "HookPayload":
        return HookPayload("InstructionsLoaded", {"paths": paths})

    @staticmethod
    def session_start(source: str = "startup", model: str = "") -> "HookPayload":
        return HookPayload("SessionStart", {"source": source, "model": model})

    @staticmethod
    def session_end(session_id: str = "") -> "HookPayload":
        return HookPayload("SessionEnd", {"session_id": session_id})

    @staticmethod
    def stop(last_assistant_message: str = "") -> "HookPayload":
        return HookPayload("Stop", {"last_assistant_message": last_assistant_message})

    @staticmethod
    def stop_failure(error: str) -> "HookPayload":
        return HookPayload("StopFailure", {"error": error})

    @staticmethod
    def subagent_start(agent_id: str, agent_type: str) -> "HookPayload":
        return HookPayload("SubagentStart", {"agent_id": agent_id, "agent_type": agent_type})

    @staticmethod
    def subagent_stop(
        agent_id: str,
        agent_type: str,
        last_assistant_message: str = "",
    ) -> "HookPayload":
        return HookPayload(
            "SubagentStop",
            {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "last_assistant_message": last_assistant_message,
            },
        )

    @staticmethod
    def config_change(key: str, old_value: Any = None, new_value: Any = None) -> "HookPayload":
        return HookPayload(
            "ConfigChange",
            {"key": key, "old_value": old_value, "new_value": new_value},
        )

    @staticmethod
    def task_created(task_id: str, subject: str) -> "HookPayload":
        return HookPayload("TaskCreated", {"task_id": task_id, "subject": subject})

    @staticmethod
    def task_completed(task_id: str, subject: str) -> "HookPayload":
        return HookPayload("TaskCompleted", {"task_id": task_id, "subject": subject})

    @staticmethod
    def worktree_create(path: str, branch: str) -> "HookPayload":
        return HookPayload("WorktreeCreate", {"path": path, "branch": branch})

    @staticmethod
    def worktree_remove(path: str) -> "HookPayload":
        return HookPayload("WorktreeRemove", {"path": path})

    @staticmethod
    def teammate_idle(agent_name: str) -> "HookPayload":
        return HookPayload("TeammateIdle", {"agent_name": agent_name})

    @staticmethod
    def setup() -> "HookPayload":
        return HookPayload("Setup", {})

    @staticmethod
    def notification(message: str, level: str = "info") -> "HookPayload":
        return HookPayload("Notification", {"message": message, "level": level})

    @staticmethod
    def file_changed(file_path: str, event: str = "change") -> "HookPayload":
        return HookPayload("FileChanged", {"file_path": file_path, "event": event})

    @staticmethod
    def elicitation(server_name: str, message: str) -> "HookPayload":
        return HookPayload("Elicitation", {"server_name": server_name, "message": message})

    @staticmethod
    def elicitation_result(server_name: str, result: dict) -> "HookPayload":
        return HookPayload(
            "ElicitationResult",
            {"server_name": server_name, "result": result},
        )


@dataclass(frozen=True)
class HookScope:
    source: str
    file_path: Path
    hooks: dict[str, Any]
    skill_root: Path | None = None


@dataclass(frozen=True)
class HookListing:
    event: str
    matcher: str | None
    hook_type: str
    command: str | None
    source: str
    file_path: str
    scope_root: str | None = None


@dataclass(frozen=True)
class HookDispatchResult:
    matched_hooks: int
    blocked: bool = False
    block_reason: str | None = None
    permission_request_result: dict[str, Any] | None = None
    watch_paths: list[str] | None = None
    worktree_path: str | None = None
    elicitation_action: str | None = None
    elicitation_content: dict[str, Any] | None = None
    retry: bool = False


_active_skill_hook_scopes: ContextVar[list[HookScope]] = ContextVar(
    "active_skill_hook_scopes",
    default=[],
)
_watched_paths: dict[str, float | None] = {}

# Tracks hooks marked ``once: true`` that have already fired.
# Key is ``(source, event_name, hook_identity)`` where hook_identity
# is the command/url/prompt text that uniquely identifies the hook.
_once_fired: set[tuple[str, str, str]] = set()


@dataclass
class HookConfig:
    """Configuration for a single hook instance."""

    type: str  # "command" | "http" | "prompt" | "agent"
    command: str = ""
    url: str = ""
    prompt: str = ""
    timeout: int = 60
    shell: str = ""  # "" means default, or "bash"/"zsh"/"sh"
    headers: dict[str, str] | None = None
    allowed_env_vars: list[str] | None = None
    async_hook: bool = False
    once: bool = False
    matcher: str = ""
    if_condition: str = ""
    model: str = ""


def _expand_env_vars(value: str, allowed_vars: list[str]) -> str:
    """Expand ``${VAR}`` in *value*, only for allowed env var names."""
    for var in allowed_vars:
        placeholder = f"${{{var}}}"
        if placeholder in value:
            value = value.replace(placeholder, os.environ.get(var, ""))
    return value


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _hooks_disabled_level(cwd: str | Path) -> str | None:
    """Return the scope level where ``disableAllHooks`` is set, or None.

    Returns ``"managed"`` when set in managed-settings.json (disables everything),
    or ``"user"`` when set in user/project/local settings (managed hooks still run).
    """
    managed = load_managed_settings()
    if managed.exists and managed.valid:
        if managed.data.get("disableAllHooks") is True:
            return "managed"
    user = harness_home_dir() / "settings.json"
    if user.exists():
        data = _load_json_file(user)
        if data.get("disableAllHooks") is True:
            return "user"
    for project_path in _project_settings_paths(cwd):
        data = _load_json_file(project_path)
        if data.get("disableAllHooks") is True:
            return "user"
    return None


def _project_settings_paths(cwd: str | Path) -> list[Path]:
    paths: list[Path] = []
    current = Path(cwd).resolve()
    home = Path.home().resolve()
    while True:
        candidate = settings_path(current)
        if candidate.exists():
            paths.append(candidate)
        local = current / ".koder" / "settings.local.json"
        if local.exists():
            paths.append(local)
        if current == home or current.parent == current:
            break
        if (current / ".git").exists():
            break
        current = current.parent
    return paths


def load_hook_scopes(cwd: str | Path) -> list[HookScope]:
    scopes: list[HookScope] = []
    user_settings = harness_home_dir() / "settings.json"
    if user_settings.exists():
        hooks = _load_json_file(user_settings).get("hooks")
        if isinstance(hooks, dict):
            scopes.append(HookScope(source="user_settings", file_path=user_settings, hooks=hooks))
    policy_settings = managed_settings_path()
    if policy_settings.exists():
        hooks = load_managed_settings(policy_settings).data.get("hooks")
        if isinstance(hooks, dict):
            scopes.append(
                HookScope(source="policy_settings", file_path=policy_settings, hooks=hooks)
            )
    for path in _project_settings_paths(cwd):
        hooks = _load_json_file(path).get("hooks")
        if isinstance(hooks, dict):
            source = "local_settings" if path.name == "settings.local.json" else "project_settings"
            scopes.append(HookScope(source=source, file_path=path, hooks=hooks))
    plugin_root = harness_home_dir() / "plugins"
    if plugin_root.exists():
        from koder_agent.harness.plugins.manifest import find_manifest
        from koder_agent.harness.plugins.state import PluginStateStore

        state_store = PluginStateStore(plugin_root / "state.json")
        for plugin_dir in sorted(path for path in plugin_root.iterdir() if path.is_dir()):
            manifest_path = find_manifest(plugin_dir)
            if manifest_path is None:
                continue
            try:
                import json as _json

                manifest_data = _json.loads(manifest_path.read_text(encoding="utf-8"))
                plugin_name = manifest_data.get("name", "")
            except Exception:
                logger.debug("Failed to read plugin manifest", exc_info=True)
                continue
            if not plugin_name or not state_store.is_enabled(plugin_name):
                continue
            hooks_path = plugin_dir / "hooks" / "hooks.json"
            if not hooks_path.exists():
                continue
            hooks = _load_json_file(hooks_path).get("hooks")
            if isinstance(hooks, dict):
                scopes.append(
                    HookScope(
                        source="plugin",
                        file_path=hooks_path,
                        hooks=hooks,
                        skill_root=plugin_dir,
                    )
                )
    scopes.extend(_active_skill_hook_scopes.get())
    return scopes


def list_configured_hooks(cwd: str | Path) -> list[HookListing]:
    listings: list[HookListing] = []
    for scope in load_hook_scopes(cwd):
        for event_name, groups in scope.hooks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                matcher = group.get("matcher")
                for hook in group.get("hooks") or []:
                    if not isinstance(hook, dict):
                        continue
                    listings.append(
                        HookListing(
                            event=event_name,
                            matcher=str(matcher) if matcher is not None else None,
                            hook_type=str(hook.get("type") or "unknown"),
                            command=(
                                str(hook.get("command"))
                                if hook.get("command") is not None
                                else None
                            ),
                            source=scope.source,
                            file_path=str(scope.file_path),
                            scope_root=str(scope.skill_root) if scope.skill_root else None,
                        )
                    )
    return listings


def _matches_matcher(matcher: str | None, match_value: str | None) -> bool:
    if matcher in (None, "", "*"):
        return True
    if match_value is None:
        return False
    try:
        return re.search(str(matcher), match_value) is not None
    except re.error:
        return str(matcher) == match_value


def _payload_target(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    for field in ("command", "file_path", "path", "args", "url"):
        value = tool_input.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _matches_if(condition: str | None, payload: dict[str, Any]) -> bool:
    if not condition:
        return True
    match = re.fullmatch(r"([A-Za-z0-9_*|.-]+)\((.*)\)", condition.strip())
    if match is None:
        return True
    tool_pattern = match.group(1).strip()
    arg_pattern = match.group(2).strip()
    tool_name = str(payload.get("tool_name") or "")
    if tool_pattern not in {"", "*"}:
        tool_patterns = [part.strip() for part in tool_pattern.split("|") if part.strip()]
        if not any(
            re.fullmatch(pattern.replace("*", ".*"), tool_name) for pattern in tool_patterns
        ):
            return False
    if arg_pattern in {"", "*"}:
        return True
    target = _payload_target(payload)
    regex = "^" + re.escape(arg_pattern).replace(r"\*", ".*") + "$"
    return re.fullmatch(regex, target) is not None


def _hook_identity(hook: dict[str, Any]) -> str:
    """Return a string that uniquely identifies a hook for deduplication."""
    hook_type = hook.get("type", "")
    if hook_type == "command":
        return f"command:{hook.get('command', '')}"
    if hook_type == "http":
        return f"http:{hook.get('url', '')}"
    if hook_type in ("prompt", "agent"):
        return f"{hook_type}:{hook.get('prompt', '')}"
    return f"unknown:{id(hook)}"


def _run_command_hook(
    *,
    command: str,
    payload_text: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int | float | None = None,
    shell: str = "",
) -> tuple[int, str, str]:
    if shell:
        # Explicit shell selection: run as [shell, "-c", command].
        cmd: str | list[str] = [shell, "-c", command]
        use_shell = False
    else:
        cmd = command
        use_shell = True
    try:
        result = subprocess.run(
            cmd,
            input=payload_text,
            text=True,
            cwd=str(cwd),
            shell=use_shell,
            capture_output=True,
            env=env,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "", "Hook timed out"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _run_http_hook(
    *,
    url: str,
    payload_text: str,
    timeout: int | float | None,
    headers: dict[str, str] | None = None,
    allowed_env_vars: list[str] | None = None,
) -> tuple[int, str, str]:
    merged_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        env_vars = allowed_env_vars or []
        for key, value in headers.items():
            merged_headers[key] = _expand_env_vars(value, env_vars) if env_vars else value
    request = urllib.request.Request(
        url,
        data=payload_text.encode("utf-8"),
        headers=merged_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, body.strip(), ""
    except Exception as exc:  # pragma: no cover - defensive
        return 500, "", str(exc)


def _run_prompt_hook(
    *,
    prompt_text: str,
    payload_text: str,
    model: str | None,
) -> str:
    from agents import RunConfig, Runner

    from koder_agent.agentic.agent import create_dev_agent

    async def _run() -> str:
        hook_prompt = (
            "You are evaluating a hook condition. "
            "Return only JSON. "
            f"Hook prompt:\n{prompt_text}\n\n"
            f"Hook input JSON:\n{payload_text}"
        )
        agent = await create_dev_agent([], name="HookPrompt", model_override=model)
        result = await Runner.run(agent, hook_prompt, run_config=RunConfig(), max_turns=5)
        return str(result.final_output or "")

    return _run_coroutine_sync(_run())


def _run_agent_hook(
    *,
    prompt_text: str,
    payload_text: str,
    model: str | None,
) -> str:
    from agents import RunConfig, Runner

    from koder_agent.agentic.agent import create_dev_agent
    from koder_agent.tools import get_all_tools

    async def _run() -> str:
        hook_prompt = (
            "You are an agent-based hook evaluator. "
            "Return only JSON. "
            f"Hook prompt:\n{prompt_text}\n\n"
            f"Hook input JSON:\n{payload_text}"
        )
        agent = await create_dev_agent(get_all_tools(), name="HookAgent", model_override=model)
        result = await Runner.run(agent, hook_prompt, run_config=RunConfig(), max_turns=10)
        return str(result.final_output or "")

    return _run_coroutine_sync(_run())


def _run_coroutine_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    holder: dict[str, Any] = {}

    def _target():
        try:
            holder["result"] = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - defensive
            holder["error"] = exc

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if "error" in holder:
        raise holder["error"]
    return holder.get("result", "")


def _extract_block(stdout: str) -> str | None:
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    if isinstance(data, dict):
        hook_specific = data.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            decision = hook_specific.get("permissionDecision") or hook_specific.get("decision")
            if isinstance(decision, dict):
                behavior = decision.get("behavior")
                if behavior in {"deny", "block"}:
                    return str(
                        decision.get("message")
                        or hook_specific.get("permissionDecisionReason")
                        or hook_specific.get("reason")
                        or "Blocked by hook"
                    )
            elif decision in {"deny", "block"}:
                return str(
                    hook_specific.get("permissionDecisionReason")
                    or hook_specific.get("reason")
                    or "Blocked by hook"
                )
        if data.get("decision") == "block":
            return str(data.get("reason") or "Blocked by hook")
    return None


def _parse_hook_output(stdout: str) -> dict[str, Any]:
    if not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_dispatch_result(
    current: HookDispatchResult, parsed: dict[str, Any]
) -> HookDispatchResult:
    hook_specific = (
        parsed.get("hookSpecificOutput")
        if isinstance(parsed.get("hookSpecificOutput"), dict)
        else {}
    )
    permission_request_result = (
        hook_specific.get("decision") if isinstance(hook_specific.get("decision"), dict) else None
    )
    return HookDispatchResult(
        matched_hooks=current.matched_hooks,
        blocked=current.blocked,
        block_reason=current.block_reason,
        permission_request_result=permission_request_result,
        watch_paths=(
            hook_specific.get("watchPaths")
            if isinstance(hook_specific.get("watchPaths"), list)
            else None
        ),
        worktree_path=(
            hook_specific.get("worktreePath")
            if isinstance(hook_specific.get("worktreePath"), str)
            else None
        ),
        elicitation_action=(
            hook_specific.get("action") if isinstance(hook_specific.get("action"), str) else None
        ),
        elicitation_content=(
            hook_specific.get("content") if isinstance(hook_specific.get("content"), dict) else None
        ),
        retry=hook_specific.get("retry") is True,
    )


def update_watch_paths(paths: list[str] | None) -> None:
    if not paths:
        return
    for raw in paths:
        path = str(Path(raw).expanduser().resolve())
        current = _watched_paths.get(path)
        if current is not None:
            continue
        resolved = Path(path)
        if resolved.exists():
            _watched_paths[path] = resolved.stat().st_mtime
        else:
            _watched_paths[path] = None


def poll_file_change_hooks(cwd: str | Path) -> int:
    fired = 0
    for path_str, previous_mtime in list(_watched_paths.items()):
        path = Path(path_str)
        current_mtime = path.stat().st_mtime if path.exists() else None
        if current_mtime == previous_mtime:
            continue
        _watched_paths[path_str] = current_mtime
        result = dispatch_command_hooks(
            cwd=cwd,
            event_name="FileChanged",
            match_value=path.name,
            payload={
                "event": "FileChanged",
                "file_path": str(path.resolve()),
            },
        )
        update_watch_paths(result.watch_paths)
        fired += 1
    return fired


def _run_async_command(
    *,
    command: str,
    payload_text: str,
    cwd: Path,
    env: dict[str, str],
    shell: str = "",
) -> None:
    if shell:
        cmd: str | list[str] = [shell, "-c", command]
        use_shell = False
    else:
        cmd = command
        use_shell = True

    def _target():
        subprocess.run(
            cmd,
            input=payload_text,
            text=True,
            cwd=str(cwd),
            shell=use_shell,
            capture_output=True,
            env=env,
            check=False,
        )

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()


def dispatch_command_hooks(
    *,
    cwd: str | Path,
    event_name: str,
    payload: dict[str, Any],
    match_value: str | None = None,
) -> HookDispatchResult:
    # Respect disableAllHooks setting.
    # "managed" level disables everything; "user" level still allows managed hooks.
    disabled_level = _hooks_disabled_level(cwd)
    if disabled_level == "managed":
        return HookDispatchResult(matched_hooks=0)

    scopes = load_hook_scopes(cwd)
    if disabled_level == "user":
        scopes = [s for s in scopes if s.source == "policy_settings"]
    payload_text = json.dumps(payload)
    result = HookDispatchResult(matched_hooks=0)
    seen_hooks: set[str] = set()
    for scope in scopes:
        groups = scope.hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            if not _matches_matcher(group.get("matcher"), match_value):
                continue
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                if not _matches_if(hook.get("if"), payload):
                    continue

                # Deduplication: skip identical hooks within a dispatch.
                identity = _hook_identity(hook)
                if identity in seen_hooks:
                    continue
                seen_hooks.add(identity)

                # Once-only: skip hooks that have already fired.
                if hook.get("once") is True:
                    once_key = (scope.source, event_name, identity)
                    if once_key in _once_fired:
                        continue
                    _once_fired.add(once_key)

                command = hook.get("command")
                env = os.environ.copy()
                env["KODER_PROJECT_DIR"] = str(Path(cwd).resolve())
                if scope.skill_root is not None:
                    env["KODER_SKILL_DIR"] = str(scope.skill_root)
                # Inject plugin env vars for plugin-sourced hooks
                if scope.source == "plugin" and scope.skill_root is not None:
                    from koder_agent.harness.plugins.env import plugin_env_vars
                    from koder_agent.harness.plugins.manifest import find_manifest

                    _mp = find_manifest(scope.skill_root)
                    if _mp is not None:
                        try:
                            import json as _json2

                            _md = _json2.loads(_mp.read_text(encoding="utf-8"))
                            _pname = _md.get("name", "")
                            if _pname:
                                env.update(plugin_env_vars(_pname, scope.skill_root))
                        except Exception:
                            logger.debug("Failed to load plugin env vars", exc_info=True)
                if isinstance(payload.get("session_id"), str):
                    env["KODER_SESSION_ID"] = str(payload["session_id"])
                    if event_name in _ENV_FILE_EVENTS:
                        env["KODER_ENV_FILE"] = str(session_env_file(str(payload["session_id"])))
                hook_type = hook.get("type")
                hook_timeout = hook.get("timeout")
                hook_shell = hook.get("shell") or ""
                hook_headers = (
                    hook.get("headers") if isinstance(hook.get("headers"), dict) else None
                )
                hook_allowed_env = (
                    hook.get("allowedEnvVars")
                    if isinstance(hook.get("allowedEnvVars"), list)
                    else None
                )
                stdout = ""
                stderr = ""
                code = 0
                if hook_type == "command":
                    if not isinstance(command, str) or not command.strip():
                        continue
                    if hook.get("async") is True:
                        result = HookDispatchResult(matched_hooks=result.matched_hooks + 1)
                        _run_async_command(
                            command=command,
                            payload_text=payload_text,
                            cwd=scope.skill_root or Path(cwd).resolve(),
                            env=env,
                            shell=hook_shell,
                        )
                        continue
                    result = HookDispatchResult(matched_hooks=result.matched_hooks + 1)
                    code, stdout, stderr = _run_command_hook(
                        command=command,
                        payload_text=payload_text,
                        cwd=scope.skill_root or Path(cwd).resolve(),
                        env=env,
                        timeout=hook_timeout,
                        shell=hook_shell,
                    )
                elif hook_type == "http":
                    url = hook.get("url")
                    if not isinstance(url, str) or not url.strip():
                        continue
                    result = HookDispatchResult(matched_hooks=result.matched_hooks + 1)
                    code, stdout, stderr = _run_http_hook(
                        url=url,
                        payload_text=payload_text,
                        timeout=hook_timeout,
                        headers=hook_headers,
                        allowed_env_vars=hook_allowed_env,
                    )
                elif hook_type == "prompt":
                    prompt_text = hook.get("prompt")
                    if not isinstance(prompt_text, str) or not prompt_text.strip():
                        continue
                    result = HookDispatchResult(matched_hooks=result.matched_hooks + 1)
                    stdout = _run_prompt_hook(
                        prompt_text=prompt_text,
                        payload_text=payload_text,
                        model=hook.get("model") if isinstance(hook.get("model"), str) else None,
                    )
                elif hook_type == "agent":
                    prompt_text = hook.get("prompt")
                    if not isinstance(prompt_text, str) or not prompt_text.strip():
                        continue
                    result = HookDispatchResult(matched_hooks=result.matched_hooks + 1)
                    stdout = _run_agent_hook(
                        prompt_text=prompt_text,
                        payload_text=payload_text,
                        model=hook.get("model") if isinstance(hook.get("model"), str) else None,
                    )
                else:
                    continue

                parsed = _parse_hook_output(stdout)
                result = _merge_dispatch_result(result, parsed)
                block_reason = _extract_block(stdout)
                if code == 2:
                    return HookDispatchResult(
                        matched_hooks=result.matched_hooks,
                        blocked=True,
                        block_reason=stderr or block_reason or "Blocked by hook",
                        permission_request_result=result.permission_request_result,
                        watch_paths=result.watch_paths,
                        worktree_path=result.worktree_path,
                        elicitation_action=result.elicitation_action,
                        elicitation_content=result.elicitation_content,
                    )
                if block_reason:
                    return HookDispatchResult(
                        matched_hooks=result.matched_hooks,
                        blocked=True,
                        block_reason=block_reason,
                        permission_request_result=result.permission_request_result,
                        watch_paths=result.watch_paths,
                        worktree_path=result.worktree_path,
                        elicitation_action=result.elicitation_action,
                        elicitation_content=result.elicitation_content,
                    )
    if event_name == "SessionStart" and isinstance(payload.get("session_id"), str):
        session_id = str(payload["session_id"])
        session_env_file(session_id)
        apply_session_env_file_to_process(session_id)
    return result


@contextmanager
def active_skill_hooks(
    skill_name: str, hooks: dict[str, Any] | None, skill_root: Path | None
) -> Iterator[None]:
    if not hooks:
        yield
        return
    scope = HookScope(
        source="skills",
        file_path=(skill_root / "SKILL.md") if skill_root is not None else Path(f"<{skill_name}>"),
        hooks=hooks,
        skill_root=skill_root,
    )
    current = list(_active_skill_hook_scopes.get())
    token = _active_skill_hook_scopes.set([*current, scope])
    try:
        yield
    finally:
        _active_skill_hook_scopes.reset(token)
