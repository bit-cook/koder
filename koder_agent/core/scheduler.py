"""Agent scheduler for managing agent execution."""

import asyncio
import json
import logging
import os
from pathlib import Path

from agents import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunConfig,
    RunItemStreamEvent,
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
)
from openai.types.responses import ResponseFunctionToolCall
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from rich.console import Group
from rich.live import Live
from rich.text import Text

from ..agentic import ApprovalHooks, create_dev_agent, get_display_hooks
from ..core.keyboard_listener import CancellationToken, escape_listener, iter_with_cancellation
from ..core.session import EnhancedSQLiteSession, migrate_legacy_sessions
from ..core.streaming_display import StreamingDisplayManager
from ..core.terminal_reflow import print_reflowable
from ..core.usage_tracker import UsageTracker, usage_snapshot_path
from ..harness.agents.definitions import (
    AgentDefinition,
    build_agent_system_prompt,
    filter_tools_for_agent_definition,
    resolve_agent_mcp_server_configs,
    resolve_agent_model,
)
from ..harness.agents.hooks import SubagentLifecycleHooks
from ..harness.buddy import (
    COMPANION_ASSISTANT_GUIDANCE,
    BuddyLiveLayout,
    buddy_runtime,
    get_companion,
    observe_turn,
)
from ..harness.config.service import RuntimeConfigService
from ..harness.memory.auto_compact import AutoCompactManager
from ..harness.memory.budget import estimate_messages_tokens
from ..harness.memory.compact import (
    compactable_session_items,
    llm_compact_messages,
    replayable_session_items,
)
from ..harness.memory.extraction import llm_extract_memories
from ..harness.memory.session_memory import SessionMemoryManager
from ..harness.reasoning_display import normalize_reasoning_display_mode
from ..tools import BackgroundShellManager, get_all_tools
from ..utils.client import get_model_name
from ..utils.model_info import get_context_window_size, get_maximum_output_tokens
from ..utils.terminal_theme import get_adaptive_console

logger = logging.getLogger(__name__)

console = get_adaptive_console()


def _reasoning_stream_payload(event_data, mode: str) -> dict | None:
    """Return display payload for SDK reasoning stream events."""

    if mode == "off":
        return None

    event_type = getattr(event_data, "type", "")
    if event_type == "response.reasoning_summary_text.delta":
        text = getattr(event_data, "delta", "") or ""
        if not text:
            return None
        return {
            "kind": "summary",
            "text": text,
            "done": False,
            "item_id": getattr(event_data, "item_id", None),
            "output_index": getattr(event_data, "output_index", 0),
            "part_index": getattr(event_data, "summary_index", 0),
        }
    if event_type == "response.reasoning_summary_text.done":
        text = getattr(event_data, "text", "") or ""
        if not text:
            return None
        return {
            "kind": "summary",
            "text": text,
            "done": True,
            "item_id": getattr(event_data, "item_id", None),
            "output_index": getattr(event_data, "output_index", 0),
            "part_index": getattr(event_data, "summary_index", 0),
        }
    if mode != "full":
        return None
    if event_type == "response.reasoning_text.delta":
        text = getattr(event_data, "delta", "") or ""
        if not text:
            return None
        return {
            "kind": "text",
            "text": text,
            "done": False,
            "item_id": getattr(event_data, "item_id", None),
            "output_index": getattr(event_data, "output_index", 0),
            "part_index": getattr(event_data, "content_index", 0),
        }
    if event_type == "response.reasoning_text.done":
        text = getattr(event_data, "text", "") or ""
        if not text:
            return None
        return {
            "kind": "text",
            "text": text,
            "done": True,
            "item_id": getattr(event_data, "item_id", None),
            "output_index": getattr(event_data, "output_index", 0),
            "part_index": getattr(event_data, "content_index", 0),
        }
    return None


class AgentScheduler:
    """Scheduler for managing agent execution with context and security."""

    def __init__(
        self,
        session_id: str = "default",
        streaming: bool = False,
        agent_definition: AgentDefinition | None = None,
        instructions_override: str | None = None,
        instructions_append: str | None = None,
        permission_service=None,
    ):
        self.semaphore = asyncio.Semaphore(10)
        self.session = EnhancedSQLiteSession(session_id=session_id)
        self.agent_definition = agent_definition
        self.instructions_override = instructions_override
        self.instructions_append = instructions_append
        self.tools = (
            filter_tools_for_agent_definition(agent_definition, get_all_tools())
            if agent_definition is not None
            else get_all_tools()
        )
        self.dev_agent = None  # Will be initialized in async method
        self.streaming = streaming
        self.permission_service = permission_service
        # Create hooks that wrap display hooks with permission checking
        display_hooks = get_display_hooks(streaming_mode=streaming)
        if agent_definition is not None:
            self.hooks = SubagentLifecycleHooks(
                agent_definition=agent_definition,
                cwd=os.getcwd(),
                wrapped_hooks=display_hooks,
                permission_service=permission_service,
            )
        else:
            self.hooks = ApprovalHooks(display_hooks, permission_service=permission_service)
        self._agent_initialized = False
        self._mcp_servers = []  # Track MCP servers for cleanup
        self.usage_tracker = UsageTracker()  # Track token usage and cost
        self.usage_path = (
            None
            if getattr(self.session, "db_path", None) == ":memory:"
            else usage_snapshot_path(session_id)
        )
        self._load_usage_snapshot()
        self._runtime_config_service = RuntimeConfigService()
        self._title_generation_task: asyncio.Task | None = None  # Async title generation
        self._migration_done = False  # Track if migration has been performed
        # Memory management - will be initialized after agent is created
        self._auto_compact: AutoCompactManager | None = None
        self._session_memory = SessionMemoryManager(project_dir=os.getcwd())
        self._tool_call_count = 0  # Track tool calls for session memory extraction
        self._static_context_tokens_cache: int | None = None
        # NOTE: micro_compact_messages is NOT wired here because the openai-agents
        # SDK's Runner manages tool results internally.  Individual tool outputs are
        # fed back into the conversation by the SDK before session.add_items is
        # called, so we have no interception point to truncate them.  To enable
        # micro-compaction the SDK would need a hook or the session's add_items
        # override would need to post-process tool-role items.

    def _has_content(self, content) -> bool:
        """Check if Rich or string content has any content."""
        if isinstance(content, str):
            return bool(content.strip())
        elif isinstance(content, Text):
            return bool(str(content).strip())
        elif isinstance(content, Group):
            return bool(content.renderables)
        else:
            return content is not None

    def _reasoning_display_mode(self) -> str:
        config = self._runtime_config_service.load()
        return normalize_reasoning_display_mode(
            os.environ.get("KODER_REASONING_DISPLAY")
            or getattr(config.harness, "reasoning_display", "off")
        )

    async def _load_memory_context(self, query: str) -> str:
        """Load relevant memories from project and user directories."""
        from pathlib import Path

        from ..harness.memory.retrieval import llm_retrieve_relevant_memories

        memory_dirs = [
            Path.cwd() / ".koder" / "memory",
            Path.home() / ".koder" / "memory",
        ]
        # Filter to existing dirs
        memory_dirs = [d for d in memory_dirs if d.exists()]

        if not memory_dirs:
            return ""

        try:
            # Use LLM-based retrieval with the query
            result = await llm_retrieve_relevant_memories(
                query=query or "",
                memory_dirs=memory_dirs,
                max_tokens=5000,
            )
            if result.memories:
                memory_lines = []
                for mem in result.memories:
                    desc = mem.parsed.description or "Memory"
                    memory_lines.append(f"# {desc}\n{mem.parsed.body}")
                return "\n\n".join(memory_lines)
        except Exception:
            pass  # Best effort - memory retrieval is optional

        return ""

    async def _ensure_agent_initialized(self):
        """Ensure the dev agent is initialized and migration is complete."""
        # Run migration once per process
        if not self._migration_done:
            await migrate_legacy_sessions(self.session.db_path)
            self._migration_done = True

        if not self._agent_initialized:
            instructions_override = None
            model_override = None
            name = "Koder"
            if self.agent_definition is not None:
                name = self.agent_definition.agent_type
                agent_prompt = build_agent_system_prompt(
                    self.agent_definition,
                    cwd=os.getcwd(),
                )
                instructions_override = self.instructions_override or agent_prompt
                model_override = resolve_agent_model(self.agent_definition)
            else:
                instructions_override = self.instructions_override
            append_segments = [segment for segment in [self.instructions_append] if segment]
            append_segments.append(COMPANION_ASSISTANT_GUIDANCE)
            self.dev_agent = await create_dev_agent(
                self.tools,
                name=name,
                instructions_override=instructions_override,
                instructions_append="\n\n".join(append_segments) if append_segments else None,
                model_override=model_override,
                extra_mcp_server_configs=(
                    resolve_agent_mcp_server_configs(self.agent_definition)
                    if self.agent_definition is not None
                    else None
                ),
            )
            # Track MCP servers for cleanup
            if hasattr(self.dev_agent, "mcp_servers") and self.dev_agent.mcp_servers:
                self._mcp_servers = list(self.dev_agent.mcp_servers)  # Create a copy

            # Initialize AutoCompactManager with model's context window
            model_name = get_model_name()
            context_window = get_context_window_size(model_name)
            max_output_tokens = get_maximum_output_tokens(model_name)
            self._auto_compact = AutoCompactManager(
                context_window=context_window,
                max_output_tokens=max_output_tokens,
            )

            self._agent_initialized = True

    async def _generate_title_background(self, user_input: str) -> None:
        """Background task to generate and save session title."""
        try:
            title = await self.session.generate_title(user_input)
            if title:
                await self.session.set_title(title)
        except Exception:
            pass  # Silent failure - best effort

    async def handle(self, user_input: str, *, render_output: bool = True) -> str:
        """Handle user input and execute agent."""
        turn_user_input = user_input

        # Ensure agent is initialized with MCP servers and migration complete
        await self._ensure_agent_initialized()

        if self.dev_agent is None:
            console.print("[dim red]Agent not initialized[/dim red]")
            return "Agent not initialized"

        await self._repair_unreplayable_session_items()

        # Note: Input panel is now displayed in InteractivePrompt, so we skip showing it here

        # Check if this is the first message for title generation and memory injection
        history = await self.session.get_items()
        if not history and self._title_generation_task is None:
            # Extract actual user request (strip context prefix if present)
            actual_request = user_input
            if "User request:" in user_input:
                actual_request = user_input.split("User request:")[-1].strip()
            self._title_generation_task = asyncio.create_task(
                self._generate_title_background(actual_request)
            )

            # Inject relevant memories on first turn
            memory_context = await self._load_memory_context(actual_request)
            if memory_context:
                # Prepend memory context to user input
                user_input = f"[Relevant memories from previous sessions]\n\n{memory_context}\n\n---\n\n{user_input}"

        if render_output:
            console.print()
            console.print("[dim]thinking...[/dim]")

        # Run the agent with session - history is managed automatically
        companion_config = self._runtime_config_service.load()
        companion = get_companion(companion_config)

        async with self.semaphore:
            buddy_runtime.mark_task_start()
            try:
                if self.streaming:
                    response = await self._handle_streaming(user_input)
                else:
                    result = await Runner.run(
                        self.dev_agent,
                        user_input,  # Just current input - session handles history
                        session=self.session,  # Automatic history management
                        run_config=RunConfig(),
                        hooks=self.hooks,
                        max_turns=50,
                    )
                    # Capture token usage from result
                    await self._capture_usage(result)

                    # Filter output for security
                    response = self._filter_output(result.final_output)

                    # Clean response output without heavy panels
                    if render_output:
                        print()  # Add space before response
                        print_reflowable(console, response)
                        print()  # Add space after response
            except Exception as e:
                # Handle execution errors gracefully
                response = (
                    f"[red]Execution error: {str(e)}[/red]\n\nPlease provide new instructions."
                )
                if render_output:
                    print_reflowable(console, response)
                return response
            finally:
                buddy_runtime.mark_task_complete()

        if companion is not None and not companion_config.harness.companion_muted:
            reaction = observe_turn(
                companion=companion,
                user_input=user_input,
                assistant_output=response,
            )
            if reaction:
                buddy_runtime.mark_observer(reaction)

        # History is automatically saved by the session
        # No manual save needed!

        await self._refresh_magic_docs_after_turn(turn_user_input, response)

        return response

    async def handle_stream_json(
        self,
        user_input: str,
        *,
        on_event,
        include_partial_messages: bool = False,
    ) -> str:
        """Handle headless stream-json execution and emit NDJSON-friendly events."""
        await self._ensure_agent_initialized()

        if self.dev_agent is None:
            raise RuntimeError("Agent not initialized")

        await self._repair_unreplayable_session_items()

        history = await self.session.get_items()
        if not history and self._title_generation_task is None:
            actual_request = user_input
            if "User request:" in user_input:
                actual_request = user_input.split("User request:")[-1].strip()
            self._title_generation_task = asyncio.create_task(
                self._generate_title_background(actual_request)
            )

        result = Runner.run_streamed(
            self.dev_agent,
            user_input,
            session=self.session,
            run_config=RunConfig(),
            hooks=self.hooks,
            max_turns=50,
        )

        partial_text_chunks: list[str] = []
        tool_names: dict[str, str] = {}
        reasoning_display_mode = self._reasoning_display_mode()

        async with self.semaphore:
            async for event in result.stream_events():
                if isinstance(event, RawResponsesStreamEvent):
                    reasoning_payload = _reasoning_stream_payload(
                        event.data,
                        reasoning_display_mode,
                    )
                    if reasoning_payload is not None:
                        if include_partial_messages:
                            delta_type = (
                                "reasoning_text_delta"
                                if reasoning_payload["kind"] == "text"
                                else "reasoning_summary_delta"
                            )
                            if reasoning_payload["done"]:
                                delta_type = delta_type.replace("_delta", "_done")
                            on_event(
                                {
                                    "type": "stream_event",
                                    "event": {
                                        "delta": {
                                            "type": delta_type,
                                            "text": reasoning_payload["text"],
                                        },
                                        "output_index": reasoning_payload["output_index"],
                                    },
                                }
                            )
                        continue

                    if isinstance(event.data, ResponseTextDeltaEvent):
                        delta_text = event.data.delta
                        if delta_text:
                            partial_text_chunks.append(delta_text)
                            if include_partial_messages:
                                on_event(
                                    {
                                        "type": "stream_event",
                                        "event": {
                                            "delta": {
                                                "type": "text_delta",
                                                "text": delta_text,
                                            },
                                            "output_index": event.data.output_index,
                                        },
                                    }
                                )
                    continue

                if not isinstance(event, RunItemStreamEvent):
                    continue

                if (
                    event.name == "tool_called"
                    and hasattr(event, "item")
                    and isinstance(event.item, ToolCallItem)
                    and isinstance(event.item.raw_item, ResponseFunctionToolCall)
                ):
                    raw_item = event.item.raw_item
                    call_id = getattr(raw_item, "call_id", None) or getattr(raw_item, "id", None)
                    if call_id:
                        tool_names[call_id] = raw_item.name
                    payload = {
                        "type": "stream_event",
                        "event": {
                            "type": "tool_called",
                            "tool_name": raw_item.name,
                        },
                    }
                    arguments = getattr(raw_item, "arguments", None)
                    if arguments:
                        payload["event"]["arguments"] = arguments
                    on_event(payload)
                    continue

                if (
                    event.name == "tool_output"
                    and hasattr(event, "item")
                    and isinstance(event.item, ToolCallOutputItem)
                ):
                    self._tool_call_count += 1
                    output_item = event.item
                    tool_call_id = getattr(output_item, "tool_call_id", None)
                    output = getattr(output_item, "output", None)
                    payload = {
                        "type": "stream_event",
                        "event": {
                            "type": "tool_output",
                            "tool_name": tool_names.get(tool_call_id),
                            "output": self._filter_output(str(output or "")),
                        },
                    }
                    on_event(payload)

        await self._capture_usage(result)

        final_response = result.final_output
        if final_response is None:
            final_response = "".join(partial_text_chunks)
        else:
            final_response = str(final_response)
        filtered_response = self._filter_output(final_response)
        await self._refresh_magic_docs_after_turn(user_input, filtered_response)
        return filtered_response

    async def _refresh_magic_docs_after_turn(self, user_input: str, response: str) -> None:
        """Best-effort Magic Doc refresh after a completed Koder turn."""

        try:
            from ..harness.magic_docs import refresh_tracked_magic_docs

            await asyncio.to_thread(
                refresh_tracked_magic_docs,
                user_input,
                response,
                cwd=Path(os.getcwd()),
            )
        except Exception:
            logger.debug("Magic Doc refresh failed", exc_info=True)

    async def _handle_streaming(self, user_input: str) -> str:
        """Handle streaming execution while preserving terminal scrollback."""
        import sys

        # Create the streaming display manager
        display_manager = StreamingDisplayManager(console)
        live_renderable = BuddyLiveLayout(
            body_getter=display_manager.get_display_content,
            config_getter=self._runtime_config_service.load,
        )

        # Check if ESC listener should be enabled (Unix TTY only)
        esc_enabled = sys.platform != "win32" and sys.stdin.isatty()

        # Add space before streaming starts
        print()

        # Run the agent in streaming mode
        if self.dev_agent is None:
            console.print("[dim red]Agent not initialized[/dim red]")
            return "Agent not initialized"

        result = Runner.run_streamed(
            self.dev_agent,
            user_input,  # Just current input - session handles history
            session=self.session,  # Automatic history management
            run_config=RunConfig(),
            hooks=self.hooks,
            max_turns=50,
        )
        reasoning_display_mode = self._reasoning_display_mode()

        # Track cancellation state with token for immediate response
        cancel_token = CancellationToken()
        cancelled = False
        execution_error = None  # Track errors for handling after Live context exits

        async def handle_escape():
            """Callback when ESC key is pressed."""
            nonlocal cancelled
            cancelled = True
            cancel_token.cancel()  # Signal to break out of iterator immediately
            result.cancel(mode="immediate")  # Also cancel the underlying stream

        # Show ESC hint if enabled (will be cleared after streaming)
        esc_hint_shown = False
        if esc_enabled:
            console.print("[dim]Press ESC to cancel[/dim]")
            esc_hint_shown = True

        def clear_esc_hint():
            nonlocal esc_hint_shown
            if esc_hint_shown and sys.stdout.isatty():
                try:
                    sys.stdout.write("\033[A\033[2K")
                    sys.stdout.flush()
                except Exception:
                    pass
                esc_hint_shown = False

        # Use Rich Live for proper formatting during streaming
        with Live(
            live_renderable,
            console=console,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="crop",
        ) as live:
            try:
                async with escape_listener(on_escape=handle_escape, enabled=esc_enabled):
                    stream_iter = result.stream_events()
                    async for event in iter_with_cancellation(stream_iter, cancel_token):
                        if cancelled:
                            break

                        try:
                            should_update = False

                            if isinstance(event, RawResponsesStreamEvent):
                                reasoning_payload = _reasoning_stream_payload(
                                    event.data,
                                    reasoning_display_mode,
                                )
                                if reasoning_payload is not None:
                                    if reasoning_payload["done"]:
                                        should_update = display_manager.handle_reasoning_done(
                                            reasoning_payload["item_id"],
                                            reasoning_payload["output_index"],
                                            reasoning_payload["text"],
                                            kind=reasoning_payload["kind"],
                                            part_index=reasoning_payload["part_index"],
                                        )
                                    else:
                                        should_update = display_manager.handle_reasoning_delta(
                                            reasoning_payload["item_id"],
                                            reasoning_payload["output_index"],
                                            reasoning_payload["text"],
                                            kind=reasoning_payload["kind"],
                                            part_index=reasoning_payload["part_index"],
                                        )
                                elif isinstance(event.data, ResponseTextDeltaEvent):
                                    delta_text = event.data.delta
                                    output_index = event.data.output_index

                                    if delta_text:
                                        should_update = display_manager.handle_text_delta(
                                            output_index, delta_text
                                        )

                            elif isinstance(event, RunItemStreamEvent):
                                if event.name == "tool_called":
                                    if (
                                        hasattr(event, "item")
                                        and isinstance(event.item, ToolCallItem)
                                        and isinstance(
                                            event.item.raw_item, ResponseFunctionToolCall
                                        )
                                    ):
                                        buddy_runtime.mark_tool_call(event.item.raw_item.name)
                                        should_update = display_manager.handle_tool_called(
                                            event.item
                                        )

                                elif event.name == "tool_output":
                                    if hasattr(event, "item") and isinstance(
                                        event.item, ToolCallOutputItem
                                    ):
                                        self._tool_call_count += 1
                                        should_update = display_manager.handle_tool_output(
                                            event.item
                                        )

                                elif event.name == "message_output_created":
                                    pass
                                elif event.name == "handoff_requested":
                                    buddy_runtime.mark_tool_call("agent_handoff")
                                    should_update = display_manager.handle_tool_called(
                                        type(
                                            "HandoffItem",
                                            (),
                                            {
                                                "raw_item": type(
                                                    "RawItem",
                                                    (),
                                                    {
                                                        "name": "agent_handoff",
                                                        "arguments": "{}",
                                                        "id": "handoff",
                                                    },
                                                )()
                                            },
                                        )()
                                    )
                                elif event.name == "handoff_occured":
                                    should_update = display_manager.handle_tool_output(
                                        type(
                                            "HandoffOutput",
                                            (),
                                            {"output": "Agent switched", "tool_call_id": "handoff"},
                                        )()
                                    )
                                elif event.name == "reasoning_item_created":
                                    if reasoning_display_mode != "off" and hasattr(event, "item"):
                                        should_update = display_manager.handle_reasoning_item(
                                            getattr(event.item, "raw_item", None),
                                            mode=reasoning_display_mode,
                                        )

                            elif isinstance(event, AgentUpdatedStreamEvent):
                                pass

                            if should_update:
                                live.refresh()

                        except Exception as e:
                            console.print(f"[dim red]Event processing error: {e}[/dim red]")

            except Exception as e:
                execution_error = e

        # After Rich Live context ends, perform intelligent cleanup
        display_manager.finalize_text_sections()

        # Clear the ESC hint line (now outside Live context)
        clear_esc_hint()

        # Handle execution error after Live context has properly closed
        if execution_error is not None:
            error_msg = f"Execution error: {str(execution_error)}"
            console.print(f"[red]{error_msg}[/red]")
            return f"{error_msg}\n\nPlease provide new instructions."

        # Handle cancellation case
        if cancelled:
            # Rich Live with transient=True clears content on exit, so we need to re-print
            # Get partial content that was accumulated during streaming (as Rich renderable)
            partial_content = display_manager.get_display_content()
            partial_text = display_manager.get_final_text()

            # Show the partial output with proper formatting (colors and markdown preserved)
            if self._has_content(partial_content):
                print()  # Add spacing
                print_reflowable(console, partial_content)
            elif partial_text and partial_text.strip():
                print()  # Add spacing
                print_reflowable(console, partial_text)

            # Show cancellation message
            console.print("\n[yellow]Operation cancelled by user[/yellow]")
            console.print()

            # Capture any usage data we can
            await self._capture_usage(result)

            # Return partial text for session history
            return partial_text or "Operation cancelled. You can provide additional instructions."

        # Get final content for permanent display (Rich Group with proper formatting)
        final_content = display_manager.get_display_content()

        # Rich Live uses in-place updates while streaming. Re-print the final
        # renderable so the completed turn is written to scrollback and remains
        # reviewable after the next prompt is drawn.
        has_content = self._has_content(final_content)
        if has_content:
            print()  # Add spacing
            print_reflowable(console, final_content)
            print()  # Add spacing after

        # Capture token usage from streaming result
        await self._capture_usage(result)

        # Get final text response for context saving
        final_response = display_manager.get_final_text()
        if not final_response:
            # Fallback to result.final_output if no text was captured
            final_response = self._filter_output(result.final_output)
        else:
            final_response = self._filter_output(final_response)

        return final_response

    def _get_display_input(self, user_input: str) -> str:
        """Get a filtered version of user input for display purposes."""
        # Check if input contains AGENTS.md content
        if "AGENTS.md content:" in user_input:
            lines = user_input.split("\n")
            filtered_lines = []
            skip_koder_content = False

            for line in lines:
                if "AGENTS.md content:" in line:
                    skip_koder_content = True
                    continue
                elif skip_koder_content and line.startswith("User request:"):
                    skip_koder_content = False
                    filtered_lines.append(line)
                elif not skip_koder_content:
                    filtered_lines.append(line)

            return "\n".join(filtered_lines)

        return user_input

    def _filter_output(self, text: str) -> str:
        """Filter sensitive information from output."""
        import re

        # Handle None or non-string input
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)

        # Filter API keys and tokens
        text = re.sub(r"sk-\w{10,}", "[TOKEN]", text)
        text = re.sub(
            r"(api[_-]?key|token|secret)[\s:=]+[\w-]{10,}", "[REDACTED]", text, flags=re.IGNORECASE
        )
        return text

    def _encode_token_count(self, text: str) -> int:
        """Estimate tokens with the session encoder, falling back to chars/4."""
        if not text:
            return 0
        try:
            encoder = getattr(self.session, "encoder", None)
            if encoder is not None:
                return len(encoder.encode(text))
        except Exception:
            pass
        return max(1, len(text) // 4)

    def _estimate_static_context_tokens(self) -> int:
        """Estimate system prompt and tool schema tokens sent with each request."""
        if self._static_context_tokens_cache is not None:
            return self._static_context_tokens_cache

        total = 0
        instructions = getattr(self.dev_agent, "instructions", None)
        if isinstance(instructions, str):
            total += self._encode_token_count(instructions)

        tool_payload = []
        tools = getattr(self.dev_agent, "tools", None) or self.tools
        for tool in tools or []:
            tool_payload.append(
                {
                    "name": getattr(tool, "name", None),
                    "description": getattr(tool, "description", None),
                    "parameters": getattr(tool, "params_json_schema", None),
                }
            )

        if tool_payload:
            try:
                total += self._encode_token_count(
                    json.dumps(tool_payload, ensure_ascii=False, default=str)
                )
            except Exception:
                total += self._encode_token_count(str(tool_payload))

        self._static_context_tokens_cache = total
        return total

    async def _estimate_session_tokens(self) -> int:
        """Estimate tokens persisted in the conversation session."""
        try:
            session_items = await self.session.get_items()
            if session_items:
                return int(self.session._estimate_tokens(session_items))
        except Exception:
            pass
        return 0

    async def refresh_context_usage_from_session(
        self,
        session_items: list[dict] | None = None,
    ) -> int:
        """Refresh status-line context tokens from the current persisted session."""
        if session_items is None:
            try:
                session_items = [
                    item for item in await self.session.get_items() if isinstance(item, dict)
                ]
            except Exception:
                session_items = []

        session_tokens = estimate_messages_tokens(session_items) if session_items else 0
        context_tokens = self._estimate_static_context_tokens() + session_tokens
        self.usage_tracker.session_usage.current_context_tokens = context_tokens
        self._save_usage_snapshot()
        return context_tokens

    async def _repair_unreplayable_session_items(self) -> None:
        """Drop invalid persisted items that would make the SDK reject the next run."""
        if not hasattr(self.session, "get_items") or not hasattr(self.session, "clear_session"):
            return
        if not hasattr(self.session, "add_items"):
            return
        try:
            items = await self.session.get_items()
        except Exception:
            return

        replayable_items = replayable_session_items(items)
        if len(replayable_items) == len(items):
            return

        try:
            await self.session.clear_session()
            saved_threshold = getattr(self.session, "summarization_threshold", None)
            if hasattr(self.session, "summarization_threshold"):
                self.session.summarization_threshold = 2**31
            try:
                await self.session.add_items(replayable_items)
            finally:
                if hasattr(self.session, "summarization_threshold"):
                    self.session.summarization_threshold = saved_threshold
            await self.refresh_context_usage_from_session(replayable_items)
        except Exception:
            logger.debug("Failed to repair unreplayable session items", exc_info=True)

    @staticmethod
    def _usage_int(obj, *names: str) -> int:
        """Read an integer usage field without letting mocks leak into math."""
        for name in names:
            value = getattr(obj, name, 0)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    def _load_usage_snapshot(self) -> None:
        if self.usage_path is None:
            return
        try:
            self.usage_tracker.load(self.usage_path)
        except Exception:
            logger.debug("Failed to load usage snapshot from %s", self.usage_path, exc_info=True)

    def _save_usage_snapshot(self) -> None:
        if self.usage_path is None:
            return
        try:
            self.usage_tracker.save(self.usage_path)
        except Exception:
            logger.debug("Failed to save usage snapshot to %s", self.usage_path, exc_info=True)

    async def _capture_usage(self, result) -> None:
        """Capture token usage from a Runner result.

        API usage is best for billing, while the status line needs the effective
        context window.  Use the largest credible context estimate so we do not
        under-report when a provider returns only per-request deltas.
        """
        try:
            input_tokens = 0
            output_tokens = 0
            context_tokens = None
            api_context_tokens = 0
            cache_read_tokens = 0
            cache_write_tokens = 0

            # Try to get usage from the API response
            if hasattr(result, "context_wrapper") and hasattr(result.context_wrapper, "usage"):
                usage = result.context_wrapper.usage
                if usage is not None:
                    input_tokens = self._usage_int(usage, "input_tokens")
                    output_tokens = self._usage_int(usage, "output_tokens")
                    cache_read_tokens = self._usage_int(
                        usage, "cache_read_input_tokens", "cache_read_tokens"
                    )
                    cache_write_tokens = self._usage_int(
                        usage, "cache_creation_input_tokens", "cache_write_tokens"
                    )

                    if hasattr(usage, "request_usage_entries") and usage.request_usage_entries:
                        last_req = usage.request_usage_entries[-1]
                        api_context_tokens = self._usage_int(last_req, "total_tokens")

                    if api_context_tokens <= 0:
                        api_context_tokens = (
                            input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
                        )

            # Fallback: estimate tokens using session's tiktoken encoder
            final_output = getattr(result, "final_output", None)
            if input_tokens <= 0 and output_tokens <= 0:
                # Estimate output tokens from final_output
                if final_output and hasattr(self.session, "encoder"):
                    output_text = str(final_output)
                    output_tokens = self._encode_token_count(output_text)

            session_tokens = await self._estimate_session_tokens()
            static_tokens = self._estimate_static_context_tokens()
            estimated_context_tokens = static_tokens + session_tokens

            if input_tokens <= 0 and session_tokens > 0:
                input_tokens = estimated_context_tokens

            if api_context_tokens > 0 or estimated_context_tokens > 0:
                context_tokens = max(api_context_tokens, estimated_context_tokens)
                if api_context_tokens <= 0 and output_tokens > 0:
                    context_tokens += output_tokens

            # Record usage if we have any tokens
            if input_tokens > 0 or output_tokens > 0:
                model_name = get_model_name()
                self.usage_tracker.record_usage(
                    input_tokens,
                    output_tokens,
                    context_tokens=context_tokens,
                    model=model_name,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
                self._save_usage_snapshot()

                # Check auto-compact threshold
                if self._auto_compact and context_tokens:
                    if self._auto_compact.should_compact(context_tokens):
                        await self._run_auto_compact()

                # Check session memory extraction trigger
                if context_tokens:
                    if self._session_memory.should_extract(context_tokens, self._tool_call_count):
                        await self._run_session_memory_extraction(
                            context_tokens, self._tool_call_count
                        )
        except Exception:
            # Silently ignore usage capture errors
            pass

    async def _run_auto_compact(self) -> None:
        """Run LLM-based auto-compaction on the session history."""
        try:
            items = await self.session.get_items()
            # Keep only items worth summarizing so compacted history stays small.
            messages = compactable_session_items(items)
            if not messages:
                return

            context_before = await self.refresh_context_usage_from_session(
                [item for item in items if isinstance(item, dict)]
            )
            console.print("[dim]compacting...[/dim]")
            result = await llm_compact_messages(messages)

            original_dict_items = [item for item in items if isinstance(item, dict)]
            compacted_items = (
                [
                    {
                        "role": "user",
                        "content": f"[Conversation compacted]\n\n{result.summary}",
                    },
                    *result.kept_messages,
                ]
                if result.summary
                else result.kept_messages
            )
            if result.summary or compacted_items != original_dict_items:
                # Replace with summary plus compact plain-text tail.

                # Replace session contents with compacted version.
                # Temporarily raise the summarization threshold so the override's
                # own token check does not re-summarize the already-compacted items.
                await self.session.clear_session()
                saved_threshold = self.session.summarization_threshold
                self.session.summarization_threshold = 2**31
                try:
                    await self.session.add_items(compacted_items)
                finally:
                    self.session.summarization_threshold = saved_threshold

                context_after = await self.refresh_context_usage_from_session(compacted_items)

                self._auto_compact.record_success()
                console.print(
                    f"[dim]compacted, context size {context_before:,} -> {context_after:,}[/dim]"
                )
            else:
                self._auto_compact.record_failure()
                logger.warning("Auto-compact produced no summary")
        except Exception as e:
            if self._auto_compact:
                self._auto_compact.record_failure()
            logger.warning("Auto-compact failed: %s", e)

    async def _run_session_memory_extraction(
        self, context_tokens: int, tool_call_count: int
    ) -> None:
        """Run LLM-based session memory extraction."""
        try:
            items = await self.session.get_items()
            messages = [
                {"role": item.get("role", "unknown"), "content": item.get("content", "")}
                for item in items
                if isinstance(item, dict)
            ]
            if not messages:
                return

            result = await llm_extract_memories(messages)

            if result.memories:
                # Persist memories to session notes file
                notes_path = self._session_memory.ensure_notes_file()
                memory_lines = []
                for mem in result.memories:
                    mem_type = mem.get("type", "reference")
                    content = mem.get("content", "")
                    memory_lines.append(f"- [{mem_type}] {content}")

                if memory_lines:
                    with open(notes_path, "a", encoding="utf-8") as f:
                        f.write("\n\n## Extracted Memories\n")
                        f.write("\n".join(memory_lines))
                        f.write("\n")

            self._session_memory.record_extraction(context_tokens, tool_call_count)
        except Exception as e:
            # Best effort - record extraction attempt so we don't retry immediately
            self._session_memory.record_extraction(context_tokens, tool_call_count)
            logger.warning("Session memory extraction failed: %s", e)

    async def cleanup(self):
        """Clean up resources, including MCP servers."""
        try:
            self._save_usage_snapshot()

            # Cancel pending title generation task
            if self._title_generation_task and not self._title_generation_task.done():
                self._title_generation_task.cancel()
                self._title_generation_task = None

            await self.reset_agent()

            # Clean up background shells
            for shell_id in list(BackgroundShellManager.get_available_ids()):
                try:
                    await BackgroundShellManager.terminate(shell_id)
                except Exception:
                    pass  # Best effort cleanup

        except Exception as e:
            console.print(f"[dim red]Unexpected error during scheduler cleanup: {e}[/dim red]")

    async def reset_agent(self):
        """Dispose the current agent so config changes apply on the next prompt."""
        try:
            if self._mcp_servers:
                for server in self._mcp_servers:
                    try:
                        if hasattr(server, "cleanup"):
                            try:
                                await asyncio.wait_for(server.cleanup(), timeout=3.0)
                            except asyncio.TimeoutError:
                                console.print(
                                    f"[dim red]MCP server {getattr(server, 'name', 'unknown')} cleanup timed out[/dim red]"
                                )
                            except Exception as cleanup_error:
                                console.print(
                                    f"[dim red]Error cleaning up MCP server {getattr(server, 'name', 'unknown')}: {cleanup_error}[/dim red]"
                                )
                    except Exception as exc:
                        console.print(
                            f"[dim red]Error accessing MCP server for cleanup: {exc}[/dim red]"
                        )
                self._mcp_servers.clear()
            self.dev_agent = None
            self._agent_initialized = False
        except Exception as exc:
            console.print(f"[dim red]Unexpected error while resetting agent: {exc}[/dim red]")
