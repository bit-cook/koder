"""Harness session flow used by interactive and prompt runtime modes."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from rich.text import Text

from koder_agent.config import get_config, get_config_manager
from koder_agent.core.terminal_reflow import CLEAR_VIEWPORT, get_reflow_buffer, print_reflowable
from koder_agent.harness.agents.definitions import (
    extract_agent_mention,
    get_agent_definitions,
    get_configured_agent_name,
)
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.hooks.runtime import (
    dispatch_command_hooks,
    poll_file_change_hooks,
    update_watch_paths,
)
from koder_agent.harness.plugins.session_root import build_session_plugin_root, default_plugin_root
from koder_agent.harness.tools.shell_executor import execute_shell_command
from koder_agent.litellm_cost_map import get_litellm_cost_map_debug_lines
from koder_agent.utils.client import get_model_name
from koder_agent.utils.terminal_theme import get_adaptive_console

logger = logging.getLogger(__name__)
console = get_adaptive_console()
DIRECT_COMMAND_PASSTHROUGHS = {"commit"}


def _parse_session_switch(slash_response: str) -> tuple[str, bool] | None:
    if slash_response.startswith("session_switch_clear:"):
        return slash_response.split(":", 1)[1], True
    if slash_response.startswith("session_switch:"):
        return slash_response.split(":", 1)[1], False
    return None


def _clear_interactive_viewport() -> None:
    get_reflow_buffer().clear()
    console.file.write(CLEAR_VIEWPORT)
    console.file.flush()


async def prompt_select_session(current_session_id: str | None = None) -> Optional[str]:
    from koder_agent.core.session import EnhancedSQLiteSession
    from koder_agent.utils import parse_session_dt, picker_arrows_with_titles

    sessions_with_titles = await EnhancedSQLiteSession.list_sessions_with_titles()
    if current_session_id is not None:
        sessions_with_titles = [
            (sid, title) for sid, title in sessions_with_titles if sid != current_session_id
        ]
    if not sessions_with_titles:
        console.print(Panel("No sessions found.", title="Sessions", border_style="yellow"))
        return None

    sessions_with_titles.sort(
        key=lambda x: (parse_session_dt(x[0])[0], parse_session_dt(x[0])[1] or None),
        reverse=True,
    )
    return picker_arrows_with_titles(sessions_with_titles)


async def load_context() -> str:
    context_info = [f"Working directory: {os.getcwd()}"]
    agents_md_path = Path(os.getcwd()) / "AGENTS.md"
    if agents_md_path.exists():
        try:
            agents_content = agents_md_path.read_text("utf-8", errors="ignore")
            hook_result = dispatch_command_hooks(
                cwd=Path.cwd(),
                event_name="InstructionsLoaded",
                match_value="session_start",
                payload={
                    "event": "InstructionsLoaded",
                    "reason": "session_start",
                    "file_path": str(agents_md_path.resolve()),
                },
            )
            update_watch_paths(hook_result.watch_paths)
            if not hook_result.blocked:
                context_info.append(f"AGENTS.md content:\n{agents_content}")
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            context_info.append(f"Error reading AGENTS.md: {exc}")
    return "\n\n".join(context_info)


def _read_piped_stdin() -> Optional[str]:
    if sys.stdin.isatty():
        return None

    stdin_text = sys.stdin.read()
    if stdin_text == "":
        return None
    return stdin_text


def _read_stream_json_messages() -> list[dict]:
    if sys.stdin.isatty():
        return []

    stdin_text = sys.stdin.read()
    if stdin_text == "":
        return []

    messages: list[dict] = []
    for line in stdin_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid --input-format stream-json payload: {exc.msg}.") from exc
        if payload.get("type") != "user":
            continue
        message = payload.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        messages.append(payload)
    return messages


def _build_prompt_text(
    *, prompt_text: Optional[list[str]], stdin_text: Optional[str]
) -> Optional[str]:
    prompt = None
    if prompt_text:
        prompt = " ".join(prompt_text).strip() or None

    if prompt and stdin_text:
        return f"{prompt}\n\nStdin content:\n{stdin_text}"
    if prompt:
        return prompt
    return stdin_text


def _build_stream_json_prompt(messages: list[dict]) -> Optional[str]:
    if not messages:
        return None

    parts: list[str] = []
    for payload in messages:
        content = payload.get("message", {}).get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(json.dumps(content, ensure_ascii=False))

    combined = "\n\n".join(part for part in parts if part)
    return combined or None


def _load_json_schema(schema_source: Optional[str]) -> Optional[dict]:
    if not schema_source:
        return None

    raw_source = schema_source.strip()
    schema_path = Path(raw_source).expanduser()
    if schema_path.exists() and schema_path.is_file():
        schema_text = schema_path.read_text("utf-8")
    else:
        schema_text = raw_source

    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid --json-schema value: {exc.msg}.") from exc

    if not isinstance(schema, dict):
        raise ValueError("--json-schema must decode to a JSON object.")
    return schema


def _load_prompt_text(
    text: Optional[str], file_path: Optional[str], *, label: str
) -> Optional[str]:
    if text is not None:
        return text
    if file_path is None:
        return None
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} file does not exist: {path}")
    return path.read_text(encoding="utf-8")


def _augment_prompt_for_json_schema(prompt: str, schema: dict) -> str:
    schema_text = json.dumps(schema, ensure_ascii=False)
    return (
        f"{prompt}\n\n"
        "Return only valid JSON that matches this schema exactly.\n"
        "Do not wrap the JSON in markdown fences or add extra commentary.\n"
        f"JSON Schema:\n{schema_text}"
    )


def _extract_structured_output(result: str, schema: dict):
    from jsonschema import ValidationError, validate

    decoder = json.JSONDecoder()
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    stripped = text.strip()
    candidates: list[str] = []

    fence_matches = re.findall(
        r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE
    )
    candidates.extend(match.strip() for match in fence_matches if match.strip())
    if stripped:
        candidates.append(stripped)

    parsed = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            pass

        for marker in ("{", "["):
            start = candidate.find(marker)
            if start == -1:
                continue
            try:
                parsed, end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if candidate[start + end :].strip():
                parsed = None
                continue
            break
        if parsed is not None:
            break

    if parsed is None:
        raise ValueError("Response did not contain valid JSON for --json-schema.")

    try:
        validate(parsed, schema)
    except ValidationError as exc:
        raise ValueError(f"Response did not match --json-schema: {exc.message}") from exc
    return parsed


def _build_usage_payload(scheduler) -> Optional[dict]:
    usage_tracker = getattr(scheduler, "usage_tracker", None)
    if usage_tracker is None:
        return None

    usage_payload: dict[str, object] = {}
    model = getattr(usage_tracker, "model", None)
    if model:
        usage_payload["model"] = model

    session_usage = getattr(usage_tracker, "session_usage", None)
    if session_usage is not None:
        usage_payload["requests"] = getattr(session_usage, "request_count", 0)
        usage_payload["input_tokens"] = getattr(session_usage, "input_tokens", 0)
        usage_payload["output_tokens"] = getattr(session_usage, "output_tokens", 0)
        usage_payload["context_tokens"] = getattr(session_usage, "current_context_tokens", 0)
        usage_payload["cost_usd"] = getattr(session_usage, "total_cost", 0.0)

    return usage_payload or None


def _write_json_line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _replay_stream_json_messages(*, scheduler, messages: list[dict]) -> None:
    session_id = scheduler.session.session_id
    for payload in messages:
        replay_payload = {
            "type": "user",
            "message": payload["message"],
            "session_id": session_id,
            "parent_tool_use_id": payload.get("parent_tool_use_id"),
            "isReplay": True,
        }
        if payload.get("uuid") is not None:
            replay_payload["uuid"] = payload["uuid"]
        if payload.get("timestamp") is not None:
            replay_payload["timestamp"] = payload["timestamp"]
        _write_json_line(replay_payload)


async def _print_json_output(
    *, scheduler, result: str, output_format: str, structured_output=None
) -> int:
    payload = {
        "output_format": output_format,
        "session_id": scheduler.session.session_id,
        "display_name": await scheduler.session.get_display_name(),
        "result": result,
    }
    usage_payload = _build_usage_payload(scheduler)
    if usage_payload is not None:
        payload["usage"] = usage_payload
    if structured_output is not None:
        payload["structured_output"] = structured_output
    if output_format == "stream-json":
        payload["type"] = "result"
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


async def run_harness_session_flow(
    *,
    first_arg: Optional[str],
    argv: list[str],
    permission_service=None,
) -> int:
    from koder_agent.core.session import EnhancedSQLiteSession

    if first_arg in DIRECT_COMMAND_PASSTHROUGHS and argv == [first_arg]:
        command_handler = HarnessInteractiveCommandHandler(emit_console=False)
        result = await command_handler.handle_slash_input(f"/{first_arg}", scheduler=None)
        if result:
            sys.stdout.write(result + "\n")
        return 0

    is_config_command = first_arg == "config"
    is_subcommand = first_arg in {"config", "auth", "mcp", "agents", "plugin", "plugins"}

    if not is_subcommand:
        from koder_agent.utils import setup_openai_client

        try:
            setup_openai_client()
        except ValueError as exc:
            console.print(Panel(f"[red]{exc}[/red]", title="Error", border_style="red"))
            return 1

    config_manager = get_config_manager()
    config = get_config() if not is_config_command else None

    from koder_agent.cli import _build_cli_parser

    parser = _build_cli_parser(first_arg)
    args = parser.parse_args(argv)
    bare_mode = bool(getattr(args, "bare", False))
    previous_simple = os.environ.get("KODER_SIMPLE")
    if bare_mode:
        os.environ["KODER_SIMPLE"] = "1"

    cli_agents_json = None
    raw_agents = getattr(args, "agents", None)
    if raw_agents:
        try:
            cli_agents_json = json.loads(raw_agents)
        except json.JSONDecodeError as exc:
            console.print(
                Panel(
                    f"Invalid --agents JSON: {exc.msg}.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug logging enabled")
        logging.debug("%s", "\n".join(get_litellm_cost_map_debug_lines()))

    if not hasattr(args, "command"):
        args.command = None

    plugin_dirs = list(getattr(args, "plugin_dir", []) or [])
    effective_plugin_root = default_plugin_root()
    if plugin_dirs:
        try:
            effective_plugin_root = build_session_plugin_root(
                getattr(args, "session", None) or first_arg or "adhoc",
                plugin_dirs,
            )
        except ValueError as exc:
            console.print(Panel(str(exc), title="Error", border_style="red"))
            return 1

    try:
        system_prompt_override = _load_prompt_text(
            getattr(args, "system_prompt", None),
            getattr(args, "system_prompt_file", None),
            label="System prompt",
        )
        if getattr(args, "system_prompt", None) and getattr(args, "system_prompt_file", None):
            raise ValueError("--system-prompt and --system-prompt-file are mutually exclusive.")
        append_segments = [
            _load_prompt_text(
                getattr(args, "append_system_prompt", None),
                None,
                label="Append system prompt",
            ),
            _load_prompt_text(
                None,
                getattr(args, "append_system_prompt_file", None),
                label="Append system prompt",
            ),
        ]
    except ValueError as exc:
        console.print(Panel(str(exc), title="Error", border_style="red"))
        return 1
    system_prompt_append = "\n\n".join(segment.strip() for segment in append_segments if segment)
    if not system_prompt_append:
        system_prompt_append = None

    if args.command == "config":
        from koder_agent.harness.config.commands import handle_config_subcommand

        return await handle_config_subcommand(args)

    if args.command == "auth":
        from koder_agent.harness.auth.commands import handle_auth_subcommand

        return await handle_auth_subcommand(args)

    if args.command == "agents":
        from koder_agent.harness.agents.commands import handle_agents_subcommand

        return await handle_agents_subcommand(args)

    if args.command in {"plugin", "plugins"}:
        from koder_agent.harness.plugins.commands import handle_plugin_subcommand

        return await handle_plugin_subcommand(args)

    if getattr(args, "continue_session", False):
        continued_session_id = await EnhancedSQLiteSession.get_most_recent_session_for_cwd(
            os.getcwd()
        )
        if not continued_session_id:
            console.print(
                Panel(
                    "No saved session found for the current directory.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        args.session = continued_session_id

    resume_value = getattr(args, "resume", None)
    if isinstance(resume_value, str) and resume_value.strip():
        args.session = resume_value.strip()
    elif resume_value:
        if not getattr(args, "session", None) and (
            not sys.stdin.isatty() or not sys.stdout.isatty()
        ):
            console.print(
                Panel(
                    "--resume requires an interactive terminal. "
                    "Use --session <id> to target a known session non-interactively.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        from koder_agent.utils import default_session_local_ms

        selected = await prompt_select_session()
        if selected:
            args.session = selected
        elif not getattr(args, "session", None):
            args.session = (
                config_manager.get_effective_value(config.cli.session, None)
                or default_session_local_ms()
            )

    if not getattr(args, "session", None):
        from koder_agent.utils import default_session_local_ms

        args.session = (
            config_manager.get_effective_value(config.cli.session, None)
            or default_session_local_ms()
        )

    await EnhancedSQLiteSession.record_session_cwd(args.session, os.getcwd())

    selected_agent_name = getattr(args, "agent", None)
    if not selected_agent_name and getattr(args, "session", None):
        selected_agent_name = await EnhancedSQLiteSession.get_session_agent(args.session)
    if not selected_agent_name:
        selected_agent_name = get_configured_agent_name(os.getcwd())

    selected_agent = None
    agent_definitions = get_agent_definitions(
        cwd=Path(os.getcwd()),
        plugin_root=effective_plugin_root,
        cli_agents_json=cli_agents_json,
    )
    if selected_agent_name:
        selected_agent = next(
            (
                agent
                for agent in agent_definitions.active_agents
                if agent.agent_type == selected_agent_name
            ),
            None,
        )
        if selected_agent is None:
            console.print(
                Panel(
                    f"Unknown --agent value: {selected_agent_name}.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        await EnhancedSQLiteSession.record_session_agent(args.session, selected_agent_name)

    # --- Channel setup ---
    from koder_agent.harness.channels.state import (
        reset_channel_state,
        set_allowed_channels,
        set_has_dev_channels,
    )
    from koder_agent.harness.channels.types import parse_channel_entries

    reset_channel_state()
    channel_entries = []

    raw_channels = getattr(args, "channels", None)
    if raw_channels:
        entries_raw = [e.strip() for e in raw_channels.split(",") if e.strip()]
        channel_entries = parse_channel_entries(entries_raw, "--channels")

    raw_dev = getattr(args, "dev_channels", None)
    if raw_dev:
        from dataclasses import replace

        dev_raw = [e.strip() for e in raw_dev.split(",") if e.strip()]
        dev_entries = [
            replace(e, dev=True)
            for e in parse_channel_entries(dev_raw, "--dangerously-load-development-channels")
        ]
        channel_entries = channel_entries + dev_entries
        set_has_dev_channels(True)

    if channel_entries:
        set_allowed_channels(channel_entries)

    if args.command == "mcp":
        from koder_agent.harness.mcp.commands import handle_mcp_subcommand

        return await handle_mcp_subcommand(args)

    context = "" if bare_mode else await load_context()

    # Check onboarding state (best-effort, non-blocking)
    if not bare_mode:
        try:
            from koder_agent.harness.onboarding import check_onboarding_state, get_onboarding_steps

            onboarding_state = check_onboarding_state(Path.cwd())
            if not onboarding_state.completed:
                missing_steps = get_onboarding_steps(onboarding_state)
                if missing_steps:
                    # Show onboarding panel to user
                    console.print(
                        Panel(
                            "\n".join(f"  • {step}" for step in missing_steps),
                            title="[yellow]Setup Recommended[/yellow]",
                            border_style="yellow",
                        )
                    )
        except Exception:
            pass  # Onboarding check is best-effort

    streaming = config.cli.stream and not args.no_stream
    input_format = getattr(args, "input_format", "text")
    if input_format == "stream-json" and getattr(args, "output_format", "text") != "stream-json":
        console.print(
            Panel(
                "--input-format=stream-json requires --output-format=stream-json.",
                title="Error",
                border_style="red",
            )
        )
        return 1
    if getattr(args, "replay_user_messages", False):
        if input_format != "stream-json" or getattr(args, "output_format", "text") != "stream-json":
            console.print(
                Panel(
                    "--replay-user-messages requires both --input-format=stream-json and --output-format=stream-json.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        if not getattr(args, "verbose", False):
            console.print(
                Panel(
                    "--replay-user-messages requires --verbose.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1

    stdin_text = None
    stream_json_messages: list[dict] = []
    if input_format == "stream-json":
        try:
            stream_json_messages = _read_stream_json_messages()
        except ValueError as exc:
            console.print(Panel(str(exc), title="Error", border_style="red"))
            return 1
    else:
        stdin_text = _read_piped_stdin()

    from koder_agent.core.file_index import ProjectFileIndex
    from koder_agent.core.interactive import InteractivePrompt
    from koder_agent.core.scheduler import AgentScheduler

    scheduler = AgentScheduler(
        session_id=args.session,
        streaming=streaming,
        agent_definition=selected_agent,
        instructions_override=system_prompt_override,
        instructions_append=system_prompt_append,
        permission_service=permission_service,
    )
    scheduler.agent_definitions = agent_definitions

    # Wire channel messages into scheduler if channels are active.
    # The router is created eagerly here so the callback is ready BEFORE
    # load_mcp_servers() runs (which happens lazily on first agent call).
    # load_mcp_servers() will pick up the existing router from the handler.
    _channel_task = None
    _cron_prompt_runner = None
    if channel_entries:
        import asyncio

        from koder_agent.harness.channels.notification import (
            ChannelNotificationRouter,
            wrap_channel_message,
        )
        from koder_agent.mcp.notifications import get_notification_handler

        _notif_handler = get_notification_handler()
        if _notif_handler.channel_router is None:
            _notif_handler.set_channel_router(ChannelNotificationRouter())

        _channel_queue: asyncio.Queue[str] = asyncio.Queue()

        async def _on_channel_msg(server_name: str, content: str, meta: dict | None) -> None:
            wrapped = wrap_channel_message(server_name, content, meta)
            await _channel_queue.put(wrapped)
            logging.getLogger(__name__).info(
                "Channel message queued from '%s' (%d bytes)", server_name, len(wrapped)
            )

        _notif_handler.channel_router.on_channel_message(_on_channel_msg)

        async def _channel_consumer() -> None:
            """Background task that drains channel messages into the scheduler."""
            while True:
                try:
                    msg = await _channel_queue.get()
                    logging.getLogger(__name__).info("Processing channel message...")
                    await scheduler.handle(msg)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logging.getLogger(__name__).error("Channel message error: %s", exc)

        # Bootstrap MCP servers eagerly so channels start receiving
        # immediately — don't wait for the first user prompt.
        await scheduler._ensure_agent_initialized()

        _channel_task = asyncio.create_task(_channel_consumer())

    if getattr(args, "name", None):
        await scheduler.session.set_title(args.name)
    command_handler = HarnessInteractiveCommandHandler(
        cli_agents_json=cli_agents_json,
        plugin_root=effective_plugin_root,
        teammate_mode=getattr(args, "teammate_mode", None),
        emit_console=getattr(args, "output_format", "text") == "text",
    )
    if not bare_mode:
        session_start_source = "resume" if resume_value else "startup"
        session_start_result = dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name="SessionStart",
            match_value=session_start_source,
            payload={
                "event": "SessionStart",
                "source": session_start_source,
                "session_id": args.session,
            },
        )
        if session_start_result.blocked:
            console.print(
                Panel(
                    session_start_result.block_reason or "Blocked by SessionStart hook.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        update_watch_paths(session_start_result.watch_paths)
    existing_session_items = await scheduler.session.get_items()
    initial_agent_prompt = (
        selected_agent.initial_prompt.strip()
        if selected_agent is not None
        and selected_agent.initial_prompt
        and not existing_session_items
        else None
    )

    try:

        async def _run_explicit_agent(agent_name: str, agent_prompt: str) -> str:
            agent = next(
                (
                    candidate
                    for candidate in agent_definitions.active_agents
                    if candidate.agent_type == agent_name
                ),
                None,
            )
            if agent is None:
                return f"Unknown agent mention: {agent_name}"
            if agent.background:
                record = await command_handler.agent_service.launch_background(
                    agent_definition=agent,
                    prompt=agent_prompt,
                    description=agent_prompt[:80],
                    seed_items=None,
                    cwd=Path.cwd(),
                )
                return (
                    "fork: launched background subagent\n"
                    f"forked_agent_id: {record.id}\n"
                    f"agent_type: {agent.agent_type}\n"
                    "status: background\n"
                    f"session_id: {record.session_id}\n"
                    f"output_file: {record.output_path}"
                )
            return await command_handler.agent_service.run_sync(
                agent_definition=agent,
                prompt=agent_prompt,
                seed_items=None,
                cwd=Path.cwd(),
            )

        command_list = command_handler.get_command_list()
        commands_dict = {name: desc for name, desc in command_list}

        file_index = ProjectFileIndex(cwd=Path.cwd())
        agent_list = [
            (a.agent_type, (a.when_to_use or "")[:60]) for a in agent_definitions.active_agents
        ]
        interactive_prompt = InteractivePrompt(
            commands_dict,
            usage_tracker=scheduler.usage_tracker,
            session_id=args.session,
            file_index=file_index,
            agents=agent_list,
            config_service=command_handler.config_service,
        )

        # Wire interactive_prompt to command_handler for vim mode and other integrations
        command_handler.interactive_prompt = interactive_prompt

        prompt_text = getattr(args, "print_prompt", None)
        has_stream_json_input = bool(stream_json_messages)
        if (
            prompt_text is not None
            and len(prompt_text) == 0
            and stdin_text is None
            and not has_stream_json_input
        ):
            console.print(
                Panel(
                    "--print requires a prompt argument.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        if getattr(args, "output_format", "text") != "text" and prompt_text is None:
            console.print(
                Panel(
                    "--output-format requires --print.",
                    title="Error",
                    border_style="red",
                )
            )
            return 1
        if getattr(args, "include_partial_messages", False):
            if getattr(args, "output_format", "text") != "stream-json":
                console.print(
                    Panel(
                        "--include-partial-messages requires --output-format stream-json.",
                        title="Error",
                        border_style="red",
                    )
                )
                return 1
            if not getattr(args, "verbose", False):
                console.print(
                    Panel(
                        "--include-partial-messages requires --verbose.",
                        title="Error",
                        border_style="red",
                    )
                )
                return 1
        json_schema = None
        if getattr(args, "json_schema", None):
            if getattr(args, "output_format", "text") != "json":
                console.print(
                    Panel(
                        "--json-schema requires --output-format json.",
                        title="Error",
                        border_style="red",
                    )
                )
                return 1
            try:
                json_schema = _load_json_schema(args.json_schema)
            except ValueError as exc:
                console.print(Panel(str(exc), title="Error", border_style="red"))
                return 1
        if prompt_text is None:
            prompt_text = getattr(args, "prompt", None)
        if input_format == "stream-json":
            prompt = _build_stream_json_prompt(stream_json_messages)
            if prompt is None:
                prompt = _build_prompt_text(prompt_text=prompt_text, stdin_text=None)
        else:
            prompt = _build_prompt_text(prompt_text=prompt_text, stdin_text=stdin_text)
        if initial_agent_prompt:
            if command_handler.is_slash_command(initial_agent_prompt):
                await command_handler.handle_slash_input(initial_agent_prompt, scheduler)
            else:
                seeded_prompt = initial_agent_prompt
                if context:
                    seeded_prompt = f"Context:\n{context}\n\nUser request: {seeded_prompt}"
                await scheduler.handle(
                    seeded_prompt,
                    render_output=getattr(args, "output_format", "text") == "text",
                )
        if prompt:
            mention = (
                None if command_handler.is_slash_command(prompt) else extract_agent_mention(prompt)
            )
            if mention:
                agent_result = await _run_explicit_agent(*mention)
                if getattr(args, "output_format", "text") in {"json", "stream-json"}:
                    return await _print_json_output(
                        scheduler=scheduler,
                        result=agent_result,
                        output_format=getattr(args, "output_format", "text"),
                    )
                print_reflowable(console, agent_result)
                return 0
            if command_handler.is_slash_command(prompt):
                if json_schema is not None:
                    console.print(
                        Panel(
                            "--json-schema is only supported for non-slash prompts in print mode.",
                            title="Error",
                            border_style="red",
                        )
                    )
                    return 1
                slash_response = await command_handler.handle_slash_input(prompt, scheduler)
                if slash_response:
                    if slash_response == "__EXIT__":
                        return 0
                    session_switch = _parse_session_switch(slash_response)
                    if session_switch:
                        new_session_id, _clear_viewport = session_switch
                        if getattr(args, "output_format", "text") in {"json", "stream-json"}:
                            return await _print_json_output(
                                scheduler=scheduler,
                                result=f"Switched to session: {new_session_id}",
                                output_format=getattr(args, "output_format", "text"),
                            )
                        print_reflowable(
                            console, f"[dim]Switched to session: {new_session_id}[/dim]"
                        )
                    else:
                        if getattr(args, "output_format", "text") in {"json", "stream-json"}:
                            return await _print_json_output(
                                scheduler=scheduler,
                                result=slash_response,
                                output_format=getattr(args, "output_format", "text"),
                            )
                        print_reflowable(
                            console,
                            Panel(
                                Text(slash_response, style="bold green"),
                                title="Command Response",
                                border_style="green",
                            ),
                        )
            elif prompt.lstrip().startswith("!"):
                shell_command = prompt.lstrip()[1:].strip()
                run_in_background = False
                if shell_command.endswith("&"):
                    shell_command = shell_command[:-1].rstrip()
                    run_in_background = True
                if not shell_command:
                    shell_result = "Usage: !<command>"
                else:
                    shell_result = (
                        await execute_shell_command(
                            shell_command,
                            run_in_background=run_in_background,
                            session_id=scheduler.session.session_id,
                        )
                    ).output
                if getattr(args, "output_format", "text") in {"json", "stream-json"}:
                    return await _print_json_output(
                        scheduler=scheduler,
                        result=shell_result,
                        output_format=getattr(args, "output_format", "text"),
                    )
                print_reflowable(
                    console,
                    Panel(
                        Text(shell_result, style="bold green"),
                        title="Shell Mode",
                        border_style="green",
                    ),
                )
                return 0
            else:
                if json_schema is not None:
                    prompt = _augment_prompt_for_json_schema(prompt, json_schema)
                if context:
                    prompt = f"Context:\n{context}\n\nUser request: {prompt}"
                poll_file_change_hooks(Path.cwd())
                submit_result = dispatch_command_hooks(
                    cwd=Path.cwd(),
                    event_name="UserPromptSubmit",
                    match_value=None,
                    payload={
                        "event": "UserPromptSubmit",
                        "prompt": prompt,
                        "session_id": args.session,
                    },
                )
                if submit_result.blocked:
                    console.print(
                        Panel(
                            submit_result.block_reason or "Blocked by UserPromptSubmit hook.",
                            title="Error",
                            border_style="red",
                        )
                    )
                    return 1
                if getattr(args, "output_format", "text") == "stream-json" and getattr(
                    args, "verbose", False
                ):
                    if getattr(args, "replay_user_messages", False):
                        await _replay_stream_json_messages(
                            scheduler=scheduler,
                            messages=stream_json_messages,
                        )
                    response = await scheduler.handle_stream_json(
                        prompt,
                        on_event=_write_json_line,
                        include_partial_messages=getattr(args, "include_partial_messages", False),
                    )
                else:
                    try:
                        from koder_agent.core.at_mentions import (
                            async_process_at_mentions,
                        )

                        prompt = await async_process_at_mentions(
                            prompt,
                            cwd=Path.cwd(),
                            active_agent_names={
                                a.agent_type for a in agent_definitions.active_agents
                            },
                            mcp_servers=getattr(scheduler, "_mcp_servers", []),
                        )
                        response = await scheduler.handle(
                            prompt,
                            render_output=getattr(args, "output_format", "text") == "text",
                        )
                    except Exception as exc:
                        dispatch_command_hooks(
                            cwd=Path.cwd(),
                            event_name="StopFailure",
                            match_value="unknown",
                            payload={
                                "event": "StopFailure",
                                "last_assistant_message": str(exc),
                                "session_id": args.session,
                            },
                        )
                        raise
                structured_output = None
                if json_schema is not None:
                    try:
                        structured_output = _extract_structured_output(response, json_schema)
                    except ValueError as exc:
                        console.print(Panel(str(exc), title="Error", border_style="red"))
                        return 1
                if getattr(args, "output_format", "text") in {"json", "stream-json"}:
                    return await _print_json_output(
                        scheduler=scheduler,
                        result=response,
                        output_format=getattr(args, "output_format", "text"),
                        structured_output=structured_output,
                    )
        else:
            # Discover MCP resources for autocomplete (best-effort).
            # The agent/MCP servers may already be initialized (channels) or
            # will be initialized lazily on the first prompt.  Attempt eagerly
            # so autocomplete has resources from the start.
            try:
                await scheduler._ensure_agent_initialized()
                if scheduler._mcp_servers and interactive_prompt.at_completer is not None:
                    from koder_agent.mcp import discover_mcp_resources

                    _mcp_res = await discover_mcp_resources(scheduler._mcp_servers)
                    if _mcp_res:
                        interactive_prompt.at_completer.update_mcp_resources(_mcp_res)
            except Exception:
                pass  # Resource discovery is best-effort

            # Start PR status background poller for the interactive loop.
            from koder_agent.harness.pr_status import PrStatusPoller

            _pr_poller = PrStatusPoller()
            if interactive_prompt.status_line is not None:
                interactive_prompt.status_line.pr_poller = _pr_poller
            _pr_poller.start()

            from koder_agent.harness.cron.runtime import CronPromptRunner

            _cron_prompt_runner = CronPromptRunner(lambda: scheduler)
            _cron_prompt_runner.start()

            if args.debug:
                print_reflowable(
                    console,
                    Panel(
                        "\n".join(get_litellm_cost_map_debug_lines()),
                        title="Debug: LiteLLM Cost Data",
                        border_style="cyan",
                    ),
                )

            while True:
                try:
                    user_input = await interactive_prompt.get_input()
                    if not user_input and not os.isatty(0):
                        break
                except (EOFError, KeyboardInterrupt):
                    break
                _pr_poller.touch()
                poll_file_change_hooks(Path.cwd())

                if user_input.lower() in {"exit", "quit"}:
                    break

                if scheduler._title_generation_task and scheduler._title_generation_task.done():
                    try:
                        display_name = await scheduler.session.get_display_name()
                        if interactive_prompt.status_line:
                            interactive_prompt.status_line.update_display_name(display_name)
                    except Exception:
                        logger.debug("Failed to update display name from title task", exc_info=True)
                    scheduler._title_generation_task = None

                if user_input:
                    if command_handler.is_slash_command(user_input):
                        # Record skill usage for recently-used sorting
                        cmd_parts = user_input.strip().lstrip("/").split()
                        cmd_name = cmd_parts[0] if cmd_parts else ""
                        if cmd_name and hasattr(interactive_prompt, "usage_tracker_skills"):
                            interactive_prompt.usage_tracker_skills.record(cmd_name)
                        slash_response = await command_handler.handle_slash_input(
                            user_input, scheduler
                        )
                        if slash_response:
                            if slash_response == "__EXIT__":
                                break
                            session_switch = _parse_session_switch(slash_response)
                            if session_switch:
                                new_session_id, clear_viewport = session_switch
                                await scheduler.cleanup()
                                scheduler = AgentScheduler(
                                    session_id=new_session_id,
                                    streaming=streaming,
                                    permission_service=permission_service,
                                )
                                interactive_prompt.update_session(new_session_id)
                                interactive_prompt.reset_history()
                                if interactive_prompt.status_line:
                                    interactive_prompt.status_line.usage_tracker = (
                                        scheduler.usage_tracker
                                    )
                                    try:
                                        display_name = await scheduler.session.get_display_name()
                                        interactive_prompt.status_line.update_display_name(
                                            display_name
                                        )
                                    except Exception:
                                        logger.debug(
                                            "Failed to update display name after session switch",
                                            exc_info=True,
                                        )
                                if clear_viewport:
                                    _clear_interactive_viewport()
                                print_reflowable(
                                    console,
                                    f"[dim]Switched to session: {new_session_id}[/dim]",
                                )
                            else:
                                print_reflowable(
                                    console,
                                    Panel(
                                        Text(slash_response, style="bold green"),
                                        title="Command Response",
                                        border_style="green",
                                    ),
                                )
                                pending_input = command_handler.consume_pending_input_text()
                                if pending_input:
                                    interactive_prompt.set_next_input_text(pending_input)
                                await interactive_prompt.refresh_prompt_suggestion(
                                    user_input,
                                    slash_response,
                                )
                    else:
                        if user_input.lstrip().startswith("!"):
                            shell_command = user_input.lstrip()[1:].strip()
                            run_in_background = False
                            if shell_command.endswith("&"):
                                shell_command = shell_command[:-1].rstrip()
                                run_in_background = True
                            if not shell_command:
                                shell_result = "Usage: !<command>"
                            else:
                                shell_result = (
                                    await execute_shell_command(
                                        shell_command,
                                        run_in_background=run_in_background,
                                        session_id=scheduler.session.session_id,
                                    )
                                ).output
                            print_reflowable(
                                console,
                                Panel(
                                    Text(shell_result, style="bold green"),
                                    title="Shell Mode",
                                    border_style="green",
                                ),
                            )
                            await interactive_prompt.refresh_prompt_suggestion(
                                user_input,
                                shell_result,
                            )
                            continue
                        mention = extract_agent_mention(user_input)
                        if mention:
                            result = await _run_explicit_agent(*mention)
                            print_reflowable(console, result)
                            await interactive_prompt.refresh_prompt_suggestion(user_input, result)
                        else:
                            submit_result = dispatch_command_hooks(
                                cwd=Path.cwd(),
                                event_name="UserPromptSubmit",
                                match_value=None,
                                payload={
                                    "event": "UserPromptSubmit",
                                    "prompt": user_input,
                                    "session_id": args.session,
                                },
                            )
                            if submit_result.blocked:
                                print_reflowable(
                                    console,
                                    Panel(
                                        submit_result.block_reason
                                        or "Blocked by UserPromptSubmit hook.",
                                        title="Command Response",
                                        border_style="red",
                                    ),
                                )
                                continue
                            try:
                                from koder_agent.core.at_mentions import (
                                    async_process_at_mentions,
                                )

                                processed_input = await async_process_at_mentions(
                                    user_input,
                                    cwd=Path.cwd(),
                                    active_agent_names={
                                        a.agent_type for a in agent_definitions.active_agents
                                    },
                                    mcp_servers=getattr(scheduler, "_mcp_servers", []),
                                )
                                # Mark response start for timing
                                interactive_prompt.mark_response_start()
                                if streaming:
                                    async with interactive_prompt.capture_queued_input(
                                        scheduler.queued_input
                                    ) as streaming_ui:
                                        response = await scheduler.handle(
                                            processed_input,
                                            streaming_ui=streaming_ui,
                                        )
                                else:
                                    response = await scheduler.handle(processed_input)
                                # Mark response complete, show tip, send notification if long-running
                                interactive_prompt.mark_response_complete(
                                    show_tip=True,
                                    context={
                                        "in_vim_mode": interactive_prompt.vim_mode_manager.enabled,
                                        "model": get_model_name(),
                                    },
                                )
                                leftover_queued_input = (
                                    scheduler.queued_input.drain_for_tool_result()
                                )
                                if leftover_queued_input:
                                    interactive_prompt.set_next_input_text(
                                        "\n\n".join(leftover_queued_input)
                                    )
                                await interactive_prompt.refresh_prompt_suggestion(
                                    user_input,
                                    response,
                                )
                            except Exception as exc:
                                dispatch_command_hooks(
                                    cwd=Path.cwd(),
                                    event_name="StopFailure",
                                    match_value="unknown",
                                    payload={
                                        "event": "StopFailure",
                                        "last_assistant_message": str(exc),
                                        "session_id": args.session,
                                    },
                                )
                                print_reflowable(
                                    console,
                                    Panel(
                                        str(exc),
                                        title="Command Response",
                                        border_style="red",
                                    ),
                                )
                                continue

                        if (
                            scheduler._title_generation_task
                            and scheduler._title_generation_task.done()
                        ):
                            try:
                                display_name = await scheduler.session.get_display_name()
                                if interactive_prompt.status_line:
                                    interactive_prompt.status_line.update_display_name(display_name)
                            except Exception:
                                logger.debug(
                                    "Failed to update display name from title task", exc_info=True
                                )
                            scheduler._title_generation_task = None
    finally:
        # Stop PR status poller if it was started.
        if "_pr_poller" in locals():
            _pr_poller.stop()
        if _channel_task is not None:
            _channel_task.cancel()
            results = await asyncio.gather(_channel_task, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.debug(
                        "Channel task cancellation failed",
                        exc_info=(type(result), result, result.__traceback__),
                    )
        if _cron_prompt_runner is not None:
            try:
                await _cron_prompt_runner.stop()
            except Exception:
                logger.debug("Cron prompt runner shutdown failed", exc_info=True)
        try:
            dispatch_command_hooks(
                cwd=Path.cwd(),
                event_name="SessionEnd",
                match_value="other",
                payload={
                    "event": "SessionEnd",
                    "reason": "other",
                    "session_id": args.session if "args" in locals() else None,
                },
            )
        except Exception:
            logger.debug("SessionEnd hook dispatch failed", exc_info=True)
        # Record session for AutoDream memory consolidation
        dream_manager = None
        try:
            import asyncio

            from koder_agent.harness.memory.auto_dream import (
                AutoDreamManager,
                default_auto_dream_task_storage,
                run_auto_dream_from_messages,
            )

            dream_state_path = Path.home() / ".koder" / "auto_dream_state.json"
            dream_manager = AutoDreamManager(state_path=dream_state_path)
            dream_manager.record_session()
            if dream_manager.should_dream() and "scheduler" in locals():
                _logger = logging.getLogger(__name__)
                result = await asyncio.wait_for(
                    run_auto_dream_from_messages(
                        await scheduler.session.get_items(),
                        manager=dream_manager,
                        task_storage=default_auto_dream_task_storage(),
                    ),
                    timeout=30,
                )
                _logger.info(
                    "AutoDream completed: memories=%d saved=%s errors=%d",
                    result.memories_written,
                    str(result.saved_path) if result.saved_path else "none",
                    len(result.errors),
                )
            else:
                dream_manager.save()
        except Exception:
            logger.debug("AutoDream memory consolidation failed", exc_info=True)
            if dream_manager is not None:
                try:
                    dream_manager.save()
                except Exception:
                    logger.debug("Failed to save dream manager state", exc_info=True)
        try:
            await scheduler.cleanup()
        except Exception:
            logger.debug("Scheduler cleanup failed", exc_info=True)
        if bare_mode:
            if previous_simple is None:
                os.environ.pop("KODER_SIMPLE", None)
            else:
                os.environ["KODER_SIMPLE"] = previous_simple

    return 0
