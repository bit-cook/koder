"""Interactive prompt with slash command completion using prompt_toolkit."""

import asyncio
import io
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from prompt_toolkit import search as prompt_search
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, merge_completers
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import (
    Condition,
    control_is_searchable,
    is_searching,
    vi_insert_mode,
)
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AppendAutoSuggestion, BeforeInput
from prompt_toolkit.shortcuts import confirm
from prompt_toolkit.widgets import Frame, SearchToolbar
from rich.console import Console

from ..config import get_config
from ..harness.buddy import MIN_COLS_FOR_FULL_SPRITE, build_prompt_toolkit_text, get_companion
from ..harness.config.service import RuntimeConfigService
from ..harness.tips import TipManager
from ..harness.voice.service import VoiceDictationController, should_start_voice_shortcut
from ..utils.client import get_model_name
from ..utils.terminal_theme import get_adaptive_console, get_adaptive_prompt_style
from .keybindings import KeybindingManager
from .terminal_reflow import attach_prompt_resize_reflow, print_reflowable
from .vim_mode import VimModeManager
from .working_indicator import working_indicator

if TYPE_CHECKING:
    from .file_index import ProjectFileIndex
    from .queued_input import QueuedInputManager
    from .usage_tracker import UsageTracker

console = get_adaptive_console()

MIN_COMPLETION_MENU_COLUMNS = 50
MIN_COMPLETION_MENU_ROWS_WITH_STATUS = 12
MIN_COMPLETION_MENU_ROWS_NO_STATUS = 10
MAX_COMPLETION_MENU_VISIBLE_ROWS = 8
MAX_INPUT_VISIBLE_LINES = 30
SHIFT_ENTER_SEQUENCES = (
    "\x1b[27;2;13~",  # xterm modifyOtherKeys.
    "\x1b[13;2u",  # CSI-u / Kitty-style encoding.
)
MIN_STATUS_LINE_ROWS = 7
INPUT_FRAME_ROWS = 3
STATUS_LINE_HEIGHT = 1
BOTTOM_PADDING_ROWS = 1
FULL_BUDDY_HEIGHT = 11
FULL_BUDDY_WIDTH = 38
COMPACT_BUDDY_HEIGHT = 1
VOICE_RECORDING_MESSAGE = "[voice] Recording... Press Space or Enter to stop."
VOICE_TRANSCRIBING_MESSAGE = "[voice] Transcribing..."


class _InputWindow(Window):
    """Wrapping input window that keeps boundary cursors on screen."""

    def _scroll_when_linewrapping(
        self,
        ui_content: UIContent,
        width: int,
        height: int,
    ) -> None:
        super()._scroll_when_linewrapping(ui_content, width, height)
        if width <= 0 or height <= 0:
            return

        cursor = ui_content.cursor_position
        line_height = ui_content.get_height_for_line(cursor.y, width, self.get_line_prefix)
        if line_height <= height - self.scroll_offsets.top:
            return

        # prompt_toolkit 3.0.52 measures only text before the cursor. When the
        # cursor starts a clipped wrapped row, that leaves vertical_scroll_2
        # one row short and the renderer falls back to terminal coordinate 0,0.
        cursor_height = ui_content.get_height_for_line(
            cursor.y,
            width,
            self.get_line_prefix,
            slice_stop=cursor.x + 1,
        )
        max_scroll = max(0, line_height - height)
        self.vertical_scroll_2 = min(
            max(self.vertical_scroll_2, cursor_height - height),
            max_scroll,
        )


def _create_input_window(buffer_control: BufferControl) -> Window:
    """Create the wrapping prompt input window with a bounded visible height."""
    return _InputWindow(
        content=buffer_control,
        height=Dimension(min=1, max=MAX_INPUT_VISIBLE_LINES),
        wrap_lines=True,
        dont_extend_height=True,
    )


def _register_shift_enter_sequences() -> bool:
    """Map distinguishable Shift+Enter encodings to the newline key action.

    ``prompt_toolkit`` currently normalizes xterm's modified Enter sequence to
    plain Enter and does not recognize the CSI-u form. Its sequence table and
    prefix cache are internal APIs, so this compatibility hook must fail open:
    Ctrl+J and Alt+Enter continue working even if those internals change.
    """
    try:
        from prompt_toolkit.input import ansi_escape_sequences, vt100_parser
        from prompt_toolkit.keys import Keys

        sequence_table = ansi_escape_sequences.ANSI_SEQUENCES
        prefix_cache = vt100_parser._IS_PREFIX_OF_LONGER_MATCH_CACHE
        if not hasattr(sequence_table, "update") or not hasattr(prefix_cache, "clear"):
            return False

        missing = object()
        originals = {
            sequence: sequence_table.get(sequence, missing) for sequence in SHIFT_ENTER_SEQUENCES
        }
        replacement = Keys.ControlJ
        try:
            sequence_table.update({sequence: replacement for sequence in SHIFT_ENTER_SEQUENCES})
            prefix_cache.clear()
        except Exception:
            try:
                for sequence, original in originals.items():
                    if original is missing:
                        sequence_table.pop(sequence, None)
                    else:
                        sequence_table[sequence] = original
            except Exception:
                pass
            return False
        return True
    except Exception:
        return False


def _should_show_status_line(*, rows: int) -> bool:
    """Return True when there is enough vertical space for the status line."""
    return rows >= MIN_STATUS_LINE_ROWS


def _should_show_bottom_padding(*, rows: int) -> bool:
    """Return True when there is enough room to keep the prompt off the terminal edge."""
    return rows >= INPUT_FRAME_ROWS + BOTTOM_PADDING_ROWS + 1


def _should_show_completion_menu(*, columns: int, rows: int, has_status_line: bool) -> bool:
    """Return True when there is enough space to render the completion popup.

    The menu is hidden in small terminals so prompt_toolkit can keep the input
    responsive instead of falling back to its built-in "Window too small..."
    placeholder window.
    """
    min_rows = (
        MIN_COMPLETION_MENU_ROWS_WITH_STATUS
        if has_status_line
        else MIN_COMPLETION_MENU_ROWS_NO_STATUS
    )
    return columns >= MIN_COMPLETION_MENU_COLUMNS and rows >= min_rows


def _get_completion_menu_max_rows(
    *,
    rows: int,
    has_status_line: bool,
    input_rows: int = INPUT_FRAME_ROWS,
    bottom_padding_rows: int = BOTTOM_PADDING_ROWS,
) -> int:
    """Return the rows left for completions after fixed UI chrome."""
    reserved_rows = (
        input_rows + (STATUS_LINE_HEIGHT if has_status_line else 0) + bottom_padding_rows
    )
    return min(MAX_COMPLETION_MENU_VISIBLE_ROWS, max(0, rows - reserved_rows))


def _get_idle_prompt_spacer_height() -> Dimension:
    """Return flexible live space above an idle prompt without fixed scrollback rows."""
    return Dimension(weight=1)


def _get_prompt_scroll_padding_rows(
    *,
    rows_below_cursor: int,
    input_rows: int,
    has_status_line: bool,
    bottom_padding_rows: int,
) -> int:
    """Return only the extra rows needed to make room for prompt chrome."""
    prompt_rows = input_rows + (STATUS_LINE_HEIGHT if has_status_line else 0) + bottom_padding_rows
    return max(0, prompt_rows - max(0, rows_below_cursor))


def _should_show_prompt_tip(
    tip_text: str | None,
    *,
    queue_mode: bool,
    has_pending_queue: bool,
) -> bool:
    """Return True when a pending tip belongs with the idle bottom prompt."""
    return bool(tip_text) and not queue_mode and not has_pending_queue


def _get_completion_menu_height(
    *,
    completion_count: int,
    max_available_height: int,
    max_visible_rows: int,
) -> Dimension:
    """Clamp completion height to the actual rows that remain on screen."""
    if completion_count <= 0 or max_available_height <= 0 or max_visible_rows <= 0:
        return Dimension.exact(0)

    height = min(completion_count, max_available_height, max_visible_rows)
    return Dimension.exact(height)


def _voice_prompt_text(status: str, detail: Optional[str] = None) -> str:
    """Return the prompt-buffer text for a voice status."""
    if status == "recording":
        return VOICE_RECORDING_MESSAGE
    if status == "transcribing":
        return VOICE_TRANSCRIBING_MESSAGE
    if status == "cancelled":
        return "Voice cancelled."
    if status == "no_text":
        return "Voice transcription returned no text."
    if status == "error":
        return f"Voice error: {detail or 'Unknown error'}"
    if status == "result":
        return detail or ""
    return detail or ""


def _accept_current_auto_suggestion(buffer: Buffer) -> bool:
    """Accept the visible auto-suggest text, including dynamically rendered suggestions."""

    if not buffer.document.is_cursor_at_the_end:
        return False

    suggestion = buffer.suggestion
    if suggestion is None:
        auto_suggest = getattr(buffer, "auto_suggest", None)
        if auto_suggest is not None:
            suggestion = auto_suggest.get_suggestion(buffer, buffer.document)

    if suggestion is None or not suggestion.text:
        return False

    auto_suggest = getattr(buffer, "auto_suggest", None)
    buffer.auto_suggest = None
    try:
        buffer.insert_text(suggestion.text)
    finally:
        buffer.auto_suggest = auto_suggest
    buffer.suggestion = None
    return True


class DynamicCompletionsMenu(CompletionsMenu):
    """CompletionsMenu that adjusts height based on available completions."""

    def __init__(
        self,
        scroll_offset: int = 1,
        extra_filter: Condition | None = None,
        max_visible_rows_getter: Callable[[], int] | None = None,
    ):
        super().__init__(
            max_height=1,
            scroll_offset=scroll_offset,
            extra_filter=extra_filter if extra_filter is not None else True,
        )
        self.max_visible_rows_getter = max_visible_rows_getter or (lambda: 0)

    def _get_completions(self, app):
        """Get current completions from the buffer."""
        buffer = app.current_buffer
        if buffer.complete_state:
            return buffer.complete_state.completions
        return []

    def preferred_height(self, width, max_available_height):
        """Calculate preferred height based on number of completions."""
        from prompt_toolkit.application import get_app

        try:
            app = get_app()
            completions = self._get_completions(app)
            return _get_completion_menu_height(
                completion_count=len(completions),
                max_available_height=max_available_height,
                max_visible_rows=self.max_visible_rows_getter(),
            )
        except Exception:
            # Fallback to no height if there's any error
            return Dimension.exact(0)


class StreamingOutputController:
    """Prompt-toolkit owned output pane for interactive streaming turns."""

    def __init__(self) -> None:
        # Empty until the first stream event; the working-indicator line above
        # the input box carries the "esc to interrupt" affordance meanwhile.
        self._text = ""
        self._app: Application | None = None
        self._final_content: Any = None
        self._final_text: str | None = None
        self._cancel_callback: Optional[Callable[[], None]] = None

    def attach_app(self, app: Application) -> None:
        self._app = app
        app.invalidate()

    def set_cancel_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Register (or clear) the scheduler's turn-cancellation hook.

        While the fixed-bottom input box is active, prompt_toolkit owns stdin,
        so the raw-terminal ESC listener cannot run; the ESC keybinding routes
        through this callback instead.
        """
        self._cancel_callback = callback

    def request_cancel(self) -> bool:
        """Dispatch a user cancellation (ESC) to the active streaming turn."""
        callback = self._cancel_callback
        if callback is None:
            return False
        callback()
        return True

    def set_message(self, text: str) -> None:
        self._text = text
        self._invalidate()

    def update_output(self, renderable: Any) -> None:
        self._text = self._render_to_text(renderable)
        self._invalidate()

    def set_final_content(self, renderable: Any) -> None:
        self._final_content = renderable

    def set_final_text(self, text: str) -> None:
        self._final_text = text

    def visible_fragments(self):
        text = self._text
        if text and not text.endswith("\n"):
            text = f"{text}\n"
        return [("class:stream-output", text)]

    def line_count(self) -> int:
        if not self._text:
            return 1
        return max(1, len(self._text.splitlines()))

    def cursor_position(self) -> Point:
        return Point(x=0, y=self.line_count() - 1)

    def render_final(self) -> None:
        if self._final_content is not None:
            print()
            print_reflowable(console, self._final_content)
            print()
        elif self._final_text:
            print()
            print_reflowable(console, self._final_text)
            print()

    def _invalidate(self) -> None:
        if self._app is not None:
            self._app.invalidate()

    def _render_to_text(self, renderable: Any) -> str:
        stream = io.StringIO()
        render_console = Console(
            file=stream,
            force_terminal=False,
            color_system=None,
            width=self._terminal_width(),
            soft_wrap=False,
        )
        render_console.print(renderable)
        return stream.getvalue().rstrip()

    def _terminal_width(self) -> int:
        if self._app is not None:
            try:
                return max(20, self._app.output.get_size().columns)
            except Exception:
                pass
        return 80


class SlashCommandCompleter(Completer):
    """Custom completer for slash commands with descriptions."""

    def __init__(self, commands: Dict[str, str], usage_tracker=None):
        """Initialize with command name -> description mapping."""
        self.commands = commands
        self._usage_tracker = usage_tracker

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        """Generate completions for slash commands."""
        text = document.text_before_cursor

        # Only show completions if we're at the start and typing a slash
        if text == "/" or (text.startswith("/") and " " not in text):
            # Remove the leading slash for matching
            search_text = text[1:] if text.startswith("/") else text

            items = list(self.commands.items())

            # Sort by recent usage when query is empty (just "/")
            if not search_text and self._usage_tracker is not None:
                items = self._usage_tracker.sort_commands(items)

            for command, description in items:
                if command.startswith(search_text):
                    if command == search_text:
                        continue
                    yield Completion(
                        text=command,
                        start_position=-len(search_text),
                        display=f"/{command}",
                        display_meta=description,
                    )


class InteractivePrompt:
    """Enhanced prompt with slash command support and status line."""

    def __init__(
        self,
        commands: Dict[str, str],
        usage_tracker: Optional["UsageTracker"] = None,
        session_id: str = "",
        file_index: Optional["ProjectFileIndex"] = None,
        agents: Optional[list[tuple[str, str]]] = None,
        config_service: RuntimeConfigService | None = None,
    ):
        """
        Initialize with available slash commands and optional status line.

        Args:
            commands: Dict of command name -> description
            usage_tracker: Optional UsageTracker for token/cost display
            session_id: Current session identifier
            file_index: Optional ProjectFileIndex for @file autocomplete
            agents: Optional list of (agent_name, description) for @agent autocomplete
        """
        self.commands = commands

        # Skill usage tracker for recently-used sorting
        from .skill_usage import SkillUsageTracker

        self.usage_tracker_skills = SkillUsageTracker()

        slash_completer = SlashCommandCompleter(commands, usage_tracker=self.usage_tracker_skills)

        self.at_completer = None
        completers: list[Completer] = [slash_completer]
        if file_index is not None:
            from .at_completer import AtMentionCompleter

            self.at_completer = AtMentionCompleter(
                file_index=file_index,
                agent_names=agents,
            )
            completers.append(self.at_completer)

        # Shell completer for ! prefix
        from .shell_completer import ShellCompleter

        completers.append(ShellCompleter())

        self.completer = merge_completers(completers)

        # Ghost text (auto-suggest)
        from .auto_suggest import KoderAutoSuggest

        self.auto_suggest = KoderAutoSuggest(commands=commands)
        self._next_input_text = ""
        self._config_service = config_service or RuntimeConfigService()
        self._voice_controller = VoiceDictationController(
            config_getter=self._config_service.load,
            model_provider_getter=lambda: (
                (get_config().model.provider or "").strip().lower()
                or get_model_name().replace("litellm/", "").split("/", 1)[0]
            ),
        )
        self._last_space_pressed_at: Optional[float] = None
        self.history = InMemoryHistory()
        # Status line (optional)
        self.status_line = None
        if usage_tracker is not None:
            from .status_line import StatusLine

            self.status_line = StatusLine(
                usage_tracker=usage_tracker,
                session_id=session_id,
            )

        # Vim mode manager
        koder_dir = Path.home() / ".koder"
        self.vim_mode_manager = VimModeManager(state_path=koder_dir / "vim_state.json")
        self.vim_mode_manager.load()

        # Keybinding manager
        self.keybinding_manager = KeybindingManager(config_path=koder_dir / "keybindings.json")

        # Tip manager
        self.tip_manager = TipManager()
        self._pending_tip_text: str | None = None

        # Response completion tracking for tips/notifications
        self._last_response_start_time: Optional[float] = None

    def _apply_keybinding_overrides(self, kb: KeyBindings) -> None:
        """Apply user-configured keybinding overrides on top of defaults."""
        from .keybindings import DEFAULT_KEYBINDINGS

        all_bindings = self.keybinding_manager.get_all_bindings()

        for action, key in all_bindings.items():
            if key is None:
                continue  # Null unbind - skip (can't easily remove existing bindings)
            default = DEFAULT_KEYBINDINGS.get(action)
            if key == default:
                continue  # No override needed

            # Add override binding for commonly customized actions
            if action == "submit":

                @kb.add(*key.split(), filter=~is_searching)
                def _submit_override(event):
                    buf = event.app.current_buffer
                    if buf.text.strip():
                        buf.validate_and_handle()

            elif action == "cancel":

                @kb.add(*key.split())
                def _cancel_override(event):
                    event.app.exit(exception=KeyboardInterrupt())

            elif action == "exit":

                @kb.add(*key.split())
                def _exit_override(event):
                    event.app.current_buffer.text = ""

    def show_tip(self, context: dict | None = None) -> None:
        """Show a contextual tip if available and not in cooldown.

        Args:
            context: Optional context dict for relevance checking (e.g., vim_mode, model).
        """
        tip = self.tip_manager.get_tip(context or {})
        self._pending_tip_text = tip

    def mark_response_start(self) -> None:
        """Mark the start of an agent response for timing notifications."""
        self._last_response_start_time = time.monotonic()

    def mark_response_complete(self, show_tip: bool = True, context: dict | None = None) -> None:
        """Mark response completion, show tip, and notify if long-running.

        Args:
            show_tip: Whether to show a tip after the response.
            context: Optional context for tip relevance.
        """
        # Show tip after response
        if show_tip:
            self.show_tip(context)
        else:
            self._pending_tip_text = None

        # Send notification for long operations
        if self._last_response_start_time is not None:
            elapsed = time.monotonic() - self._last_response_start_time
            if elapsed > 30:  # Over 30 seconds
                from .notifications import notify

                notify("Koder", "Task completed")
            self._last_response_start_time = None

    async def refresh_prompt_suggestion(
        self,
        user_input: str,
        assistant_output: str,
    ) -> str | None:
        """Generate the next empty-prompt ghost suggestion after a completed turn."""

        from ..harness.prompt_suggestion import (
            PromptSuggestionEngine,
            prompt_suggestions_enabled,
        )

        if not prompt_suggestions_enabled():
            self.auto_suggest.clear_speculative_suggestion()
            return None

        history = list(self.history.get_strings())
        engine = PromptSuggestionEngine(history)
        suggestion = await asyncio.to_thread(
            engine.suggest_next_prompt,
            user_input,
            assistant_output,
        )
        if suggestion:
            self.auto_suggest.set_speculative_suggestion(suggestion)
        else:
            self.auto_suggest.clear_speculative_suggestion()
        return suggestion

    async def get_input(self) -> str:
        """Get user input with Rich panel display and prompt_toolkit completion."""
        result = await self._run_input_app()
        if result is None:
            raise EOFError("Empty input received")
        return result.strip()

    @asynccontextmanager
    async def capture_queued_input(self, queue_manager: "QueuedInputManager"):
        """Keep a bottom input box alive while a response is streaming."""
        stop_event = asyncio.Event()
        stream_output = StreamingOutputController()
        task = asyncio.create_task(
            self._run_input_app(
                queue_manager=queue_manager,
                stop_event=stop_event,
                stream_output=stream_output,
            )
        )
        try:
            await asyncio.sleep(0)
            yield stream_output
        finally:
            stop_event.set()
            with suppress(asyncio.CancelledError, EOFError, KeyboardInterrupt):
                await task
            stream_output.render_final()

    async def _run_input_app(
        self,
        *,
        queue_manager: "QueuedInputManager | None" = None,
        stop_event: asyncio.Event | None = None,
        stream_output: StreamingOutputController | None = None,
    ) -> str | None:
        """Run the prompt application once, or continuously for queued input."""
        _register_shift_enter_sequences()
        voice_message_visible = False
        queue_mode = queue_manager is not None
        initial_text = "" if queue_mode else self._next_input_text
        if not queue_mode:
            self._next_input_text = ""

        # Create buffer
        buffer = Buffer(
            completer=self.completer,
            complete_while_typing=True,
            read_only=Condition(lambda: self._voice_controller.is_busy),
            auto_suggest=self.auto_suggest,
            history=self.history,
            document=Document(initial_text, cursor_position=len(initial_text)),
        )
        initial_suggestion = self.auto_suggest.get_suggestion(buffer, buffer.document)
        if initial_suggestion is not None:
            buffer.suggestion = initial_suggestion

        def submit_buffer(app: Application) -> None:
            """Record and submit the current prompt buffer."""
            nonlocal voice_message_visible
            text = buffer.text
            if queue_manager is not None:
                if text.strip():
                    self.auto_suggest.record_input(text)
                    self.history.append_string(text)
                    queue_manager.enqueue(text)
                self._pending_tip_text = None
                buffer.set_document(Document(""), bypass_readonly=True)
                voice_message_visible = False
                app.invalidate()
                return
            self.auto_suggest.record_input(text)
            self.history.append_string(text)
            self._pending_tip_text = None
            app.exit(result=text)

        def submit_search_match(app: Application) -> None:
            """Accept and submit the active history-search match."""
            prompt_search.accept_search()
            submit_buffer(app)

        def submit_search_buffer(_search_buffer: Buffer) -> bool:
            """Submit the active history-search match from the search buffer."""
            submit_search_match(get_app())
            return False

        search_key_bindings = KeyBindings()

        @search_key_bindings.add("enter", eager=True)
        @search_key_bindings.add("c-m", eager=True)
        @search_key_bindings.add("c-j", eager=True)
        def submit_search_key(event):
            submit_search_match(event.app)

        # Key bindings are attached both to the input control and the app so
        # prompt-editing keys can override BufferControl defaults.
        kb = KeyBindings()
        self._apply_keybinding_overrides(kb)

        if stream_output is not None:
            # While streaming, prompt_toolkit owns stdin, so the raw-terminal
            # ESC listener in the scheduler cannot see key presses. Route a
            # standalone ESC through the streaming controller to cancel the
            # in-flight turn. Non-eager so escape-prefixed sequences (arrow
            # keys, Alt+Enter) keep working; excluded in vi insert mode so ESC
            # still exits to normal mode first (press ESC again to cancel).
            @kb.add("escape", filter=~is_searching & ~vi_insert_mode)
            def cancel_streaming_turn(event):
                stream_output.request_cancel()

        # Create buffer control with "> " prefix and ghost text
        search_toolbar = SearchToolbar(search_buffer=Buffer(accept_handler=submit_search_buffer))
        search_toolbar.control.key_bindings = search_key_bindings
        buffer_control = BufferControl(
            buffer=buffer,
            search_buffer_control=search_toolbar.control,
            key_bindings=kb,
            input_processors=[
                BeforeInput("> "),
                AppendAutoSuggestion(),
            ],
            preview_search=True,
        )

        # Create input window with dynamic height
        input_window = _create_input_window(buffer_control)

        # Create simple frame without heavy styling
        framed_input = Frame(
            body=input_window,
            title="⚡ Koder",
        )

        def _runtime_config():
            return self._config_service.load()

        def _buddy_visible() -> bool:
            config = _runtime_config()
            return get_companion(config) is not None and not config.harness.companion_muted

        def _buddy_is_full() -> bool:
            return _buddy_visible() and _get_terminal_size()[0] >= MIN_COLS_FOR_FULL_SPRITE

        def _buddy_is_compact() -> bool:
            return _buddy_visible() and _get_terminal_size()[0] < MIN_COLS_FOR_FULL_SPRITE

        def _input_reserved_rows() -> int:
            if _buddy_is_full():
                return max(INPUT_FRAME_ROWS, FULL_BUDDY_HEIGHT)
            if _buddy_is_compact():
                return INPUT_FRAME_ROWS + COMPACT_BUDDY_HEIGHT
            return INPUT_FRAME_ROWS

        def _bottom_padding_rows() -> int:
            return (
                BOTTOM_PADDING_ROWS
                if _should_show_bottom_padding(rows=_get_terminal_size()[1])
                else 0
            )

        buddy_control = FormattedTextControl(
            lambda: build_prompt_toolkit_text(
                _runtime_config(),
                columns=_get_terminal_size()[0],
                show_reaction=True,
            )
        )
        buddy_full_window = Window(
            content=buddy_control,
            width=Dimension.exact(FULL_BUDDY_WIDTH),
            height=Dimension.exact(FULL_BUDDY_HEIGHT),
            dont_extend_width=True,
            dont_extend_height=True,
        )
        buddy_compact_window = Window(
            content=buddy_control,
            height=Dimension.exact(COMPACT_BUDDY_HEIGHT),
            dont_extend_height=True,
        )

        def _queued_lines_text():
            if queue_manager is None:
                return []
            lines = queue_manager.visible_lines()
            fragments = []
            for index, line in enumerate(lines):
                suffix = "\n" if index < len(lines) - 1 else ""
                fragments.append(("class:queued-input", f"{line}{suffix}"))
            return fragments

        queued_lines_window = Window(
            content=FormattedTextControl(_queued_lines_text),
            dont_extend_height=True,
        )

        def _tip_line_text():
            if not self._pending_tip_text:
                return []
            return [("class:tip-line", self._pending_tip_text)]

        tip_line_window = Window(
            content=FormattedTextControl(_tip_line_text),
            height=Dimension.exact(1),
            wrap_lines=False,
            dont_extend_height=True,
        )

        def _working_line_text():
            if stream_output is None or not working_indicator.is_active:
                return []
            head, detail = working_indicator.status_parts()
            return [
                ("class:working-indicator", f"{head} "),
                ("class:working-indicator-detail", detail),
            ]

        working_line_window = Window(
            content=FormattedTextControl(_working_line_text),
            height=Dimension.exact(1),
            wrap_lines=False,
            dont_extend_height=True,
        )

        def _stream_output_scroll(window: Window) -> int:
            visible_height = 1
            if window.render_info is not None:
                visible_height = max(1, window.render_info.window_height)
            return max(0, stream_output.line_count() - visible_height) if stream_output else 0

        stream_output_window = None
        if stream_output is not None:
            stream_output_window = Window(
                content=FormattedTextControl(
                    stream_output.visible_fragments,
                    show_cursor=False,
                    get_cursor_position=stream_output.cursor_position,
                ),
                height=Dimension(weight=1, min=1),
                wrap_lines=False,
                get_vertical_scroll=_stream_output_scroll,
                always_hide_cursor=True,
            )

        @kb.add("right", eager=True)
        @kb.add("escape", "[", "C", eager=True)
        @kb.add("escape", "O", "C", eager=True)
        def accept_suggestion(event):
            """Accept ghost text suggestion with Right arrow."""
            b = event.app.current_buffer
            if not _accept_current_auto_suggestion(b):
                # Default right-arrow behaviour: move cursor
                b.cursor_right()

        @kb.add("c-r", filter=control_is_searchable & ~is_searching, eager=True)
        def start_history_search(event):
            """Enter reverse incremental history search."""
            prompt_search.start_search(direction=prompt_search.SearchDirection.BACKWARD)

        @kb.add("c-r", filter=is_searching, eager=True)
        @kb.add("up", filter=is_searching, eager=True)
        def continue_history_search_backward(event):
            """Cycle backward through reverse-search matches."""
            prompt_search.do_incremental_search(
                prompt_search.SearchDirection.BACKWARD,
                count=event.arg,
            )

        @kb.add("down", filter=is_searching, eager=True)
        def continue_history_search_forward(event):
            """Cycle forward through reverse-search matches."""
            prompt_search.do_incremental_search(
                prompt_search.SearchDirection.FORWARD,
                count=event.arg,
            )

        @kb.add("enter", filter=is_searching, eager=True)
        @kb.add("c-m", filter=is_searching, eager=True)
        @kb.add("c-j", filter=is_searching, eager=True)
        def accept_history_search_and_submit(event):
            """Accept the current search match and execute it immediately."""
            submit_search_match(event.app)

        @kb.add("tab", filter=is_searching, eager=True)
        @kb.add("escape", filter=is_searching, eager=True)
        def accept_history_search(event):
            """Accept the current search match and continue editing."""
            prompt_search.accept_search()

        @kb.add("c-c", filter=is_searching, eager=True)
        def cancel_history_search(event):
            """Abort reverse-search and restore the original input."""
            prompt_search.stop_search()

        @kb.add("enter", eager=True)
        def accept_input(event):
            if self._voice_controller.is_recording:
                event.app.create_background_task(stop_voice(event.app))
                return
            if self._voice_controller.is_busy:
                return
            if voice_message_visible:
                _set_buffer_text(event.app, "", from_voice=False)
                return
            b = event.app.current_buffer
            if b.complete_state and b.document.text_before_cursor.startswith("/"):
                slash_text = b.document.text_before_cursor[1:]
                if slash_text and slash_text not in self.commands:
                    completion = getattr(b.complete_state, "current_completion", None)
                    if completion is None and b.complete_state.completions:
                        completion = b.complete_state.completions[0]
                    if completion is not None:
                        b.apply_completion(completion)
            submit_buffer(event.app)

        @kb.add("c-j")  # For Ctrl+Enter in many terminals
        @kb.add("escape", "enter")  # For Alt+Enter as a more compatible option
        def insert_newline(event):
            # Insert a newline for multi-line input
            event.app.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def cancel_input(event):
            nonlocal voice_message_visible
            if self._voice_controller.is_busy:
                self._voice_controller.cancel()
                _set_voice_notice(event.app, None)
                _set_buffer_text(event.app, _voice_prompt_text("cancelled"), from_voice=True)
                return
            event.app.exit(exception=KeyboardInterrupt())

        @kb.add("c-d")
        def clear_input(event):
            # Clear the input content
            event.app.current_buffer.text = ""
            nonlocal voice_message_visible
            voice_message_visible = False

        # Add Tab key for completion navigation
        @kb.add(" ")
        def handle_space(event):
            nonlocal voice_message_visible
            if self._voice_controller.is_recording:
                event.app.create_background_task(stop_voice(event.app))
                return
            if self._voice_controller.is_busy:
                return

            now = time.monotonic()
            if should_start_voice_shortcut(
                buffer_text=buffer.text,
                cursor_position=buffer.cursor_position,
                last_space_at=self._last_space_pressed_at,
                now=now,
                enabled=self._voice_controller.is_enabled(),
                busy=self._voice_controller.is_busy,
            ):
                buffer.set_document(Document(""), bypass_readonly=True)
                self._last_space_pressed_at = None
                event.app.create_background_task(start_voice(event.app))
                return

            event.app.current_buffer.insert_text(" ")
            voice_message_visible = False
            if (
                self._voice_controller.is_enabled()
                and buffer.text == " "
                and buffer.cursor_position == 1
            ):
                self._last_space_pressed_at = now
            else:
                self._last_space_pressed_at = None

        @kb.add("tab", eager=True)
        @kb.add("c-i", eager=True)
        def complete(event):
            b = event.app.current_buffer

            # Accept ghost text suggestion if present and no completion menu
            if not b.complete_state and _accept_current_auto_suggestion(b):
                return

            if b.complete_state:
                # Common prefix Tab expansion for @ mentions
                from .at_completer import _find_at_trigger
                from .file_index import find_common_prefix

                text = b.document.text_before_cursor
                if _find_at_trigger(text) is not None:
                    completions = b.complete_state.completions
                    if len(completions) > 1:
                        # Get completion texts (strip @ prefix)
                        texts = [c.text.lstrip("@").rstrip(" ").strip('"') for c in completions]
                        prefix = find_common_prefix(texts)
                        # What the user has typed after @
                        at_pos = _find_at_trigger(text)
                        if at_pos is not None:
                            current_query = text[at_pos + 1 :].lstrip('"')
                            if len(prefix) > len(current_query):
                                # Expand to common prefix, keep menu open
                                new_text = text[: at_pos + 1] + prefix
                                b.text = new_text
                                b.cursor_position = len(new_text)
                                b.cancel_completion()
                                b.start_completion(select_first=False)
                                return
                b.complete_next()
            else:
                b.start_completion(select_first=True)

        @kb.add("s-tab")  # Shift+Tab
        def complete_previous(event):
            # Shift+Tab to navigate backwards through completions
            b = event.app.current_buffer
            if b.complete_state:
                b.complete_previous()

        def _should_retrigger_completion(b) -> bool:
            """Check if completion should retrigger after editing."""
            text = b.document.text_before_cursor
            if text.startswith("/") and " " not in text:
                return True
            # Check for @ trigger in current text
            from .at_completer import _find_at_trigger

            if _find_at_trigger(text) is not None:
                return True
            return False

        @kb.add("backspace")
        def handle_backspace(event):
            nonlocal voice_message_visible
            # Handle backspace and retrigger completion if needed
            b = event.app.current_buffer
            # First perform the backspace
            b.delete_before_cursor()
            # Retrigger completion if still in a completable context
            b.cancel_completion()
            if _should_retrigger_completion(b):
                b.start_completion(select_first=False)
            voice_message_visible = False

        @kb.add("delete")
        def handle_delete(event):
            nonlocal voice_message_visible
            # Handle delete key and retrigger completion if needed
            b = event.app.current_buffer
            # First perform the delete
            b.delete()
            # Retrigger completion if still in a completable context
            b.cancel_completion()
            if _should_retrigger_completion(b):
                b.start_completion(select_first=False)
            voice_message_visible = False

        def _get_terminal_size():
            from prompt_toolkit.application import get_app

            try:
                size = get_app().output.get_size()
                return size.columns, size.rows
            except Exception:
                return 80, 24

        show_status_line = Condition(
            lambda: (
                self.status_line is not None
                and _should_show_status_line(rows=_get_terminal_size()[1])
            )
        )
        show_completion_menu = Condition(
            lambda: (
                lambda size: _should_show_completion_menu(
                    columns=size[0],
                    rows=size[1],
                    has_status_line=self.status_line is not None
                    and _should_show_status_line(rows=size[1]),
                )
            )(_get_terminal_size())
        )

        # Create layout with completion menu and optional status line
        components = []
        if stream_output_window is not None:
            components.append(stream_output_window)
        else:
            components.append(
                Window(
                    height=_get_idle_prompt_spacer_height(),
                    dont_extend_height=False,
                )
            )
        if stream_output is not None:
            # Working indicator sits between the stream pane and the queued
            # lines; refresh_interval re-evaluates the filter and text, so the
            # animation ticks without explicit invalidation.
            components.append(
                ConditionalContainer(
                    working_line_window,
                    filter=Condition(lambda: working_indicator.is_active),
                )
            )
        if queue_manager is not None:
            components.append(
                ConditionalContainer(
                    queued_lines_window,
                    filter=Condition(lambda: queue_manager.has_pending()),
                )
            )
        components.append(
            ConditionalContainer(
                tip_line_window,
                filter=Condition(
                    lambda: _should_show_prompt_tip(
                        self._pending_tip_text,
                        queue_mode=queue_manager is not None,
                        has_pending_queue=(
                            queue_manager.has_pending() if queue_manager is not None else False
                        ),
                    )
                ),
            )
        )
        components.extend(
            [
                ConditionalContainer(
                    VSplit([framed_input, buddy_full_window], padding=1),
                    filter=Condition(_buddy_is_full),
                ),
                ConditionalContainer(
                    framed_input,
                    filter=Condition(lambda: not _buddy_is_full()),
                ),
                ConditionalContainer(
                    buddy_compact_window,
                    filter=Condition(_buddy_is_compact),
                ),
                search_toolbar,
                ConditionalContainer(
                    DynamicCompletionsMenu(
                        scroll_offset=1,
                        max_visible_rows_getter=lambda: _get_completion_menu_max_rows(
                            rows=_get_terminal_size()[1],
                            has_status_line=self.status_line is not None
                            and _should_show_status_line(rows=_get_terminal_size()[1]),
                            input_rows=_input_reserved_rows(),
                            bottom_padding_rows=_bottom_padding_rows(),
                        ),
                    ),
                    filter=show_completion_menu,
                ),
            ]
        )
        if self.status_line:
            components.append(
                ConditionalContainer(self.status_line.create_window(), filter=show_status_line)
            )
        components.append(
            ConditionalContainer(
                Window(
                    height=Dimension.exact(BOTTOM_PADDING_ROWS),
                    dont_extend_height=True,
                ),
                filter=Condition(lambda: _should_show_bottom_padding(rows=_get_terminal_size()[1])),
            )
        )

        layout = Layout(HSplit(components))

        def _set_buffer_text(app: Application, text: str, *, from_voice: bool) -> None:
            nonlocal voice_message_visible
            buffer.set_document(Document(text, cursor_position=len(text)), bypass_readonly=True)
            voice_message_visible = from_voice
            app.invalidate()

        def _set_voice_notice(app: Application, notice: Optional[str]) -> None:
            if self.status_line:
                self.status_line.set_notice(notice)
            app.invalidate()

        async def start_voice(app: Application) -> None:
            try:
                await self._voice_controller.start_recording(
                    on_status=lambda value: (
                        _set_voice_notice(app, None),
                        _set_buffer_text(
                            app,
                            _voice_prompt_text(value),
                            from_voice=value in {"recording", "transcribing"},
                        ),
                    )
                )
            except Exception as exc:
                _set_voice_notice(app, None)
                _set_buffer_text(app, _voice_prompt_text("error", str(exc)), from_voice=True)

        async def stop_voice(app: Application) -> None:
            try:
                transcript = await self._voice_controller.stop_recording(
                    on_status=lambda value: (
                        _set_voice_notice(app, None),
                        (
                            _set_buffer_text(
                                app,
                                _voice_prompt_text(value),
                                from_voice=value in {"recording", "transcribing"},
                            )
                            if value
                            else None
                        ),
                    ),
                    on_partial=lambda text: _set_buffer_text(app, text, from_voice=False),
                )
            except Exception as exc:
                _set_voice_notice(app, None)
                _set_buffer_text(app, _voice_prompt_text("error", str(exc)), from_voice=True)
                return

            final_text = transcript.strip()
            if not final_text:
                _set_voice_notice(app, None)
                _set_buffer_text(app, _voice_prompt_text("no_text"), from_voice=True)
                return
            _set_voice_notice(app, None)
            _set_buffer_text(app, _voice_prompt_text("result", final_text), from_voice=False)
            await asyncio.sleep(0.12)
            app.exit(result=final_text)

        # Adaptive style that works with both light and dark terminals
        style = get_adaptive_prompt_style()

        # Create application
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            erase_when_done=stream_output is not None,
            mouse_support=False,  # Disable mouse support to allow terminal scrolling
            refresh_interval=0.25,
            editing_mode=self.vim_mode_manager.get_editing_mode(),
        )
        attach_prompt_resize_reflow(app)
        if stream_output is not None:
            stream_output.attach_app(app)

        remove_queue_callback = None
        if queue_manager is not None:
            remove_queue_callback = queue_manager.on_change(app.invalidate)

        async def _run_until_stopped() -> str | None:
            run_task = asyncio.create_task(app.run_async())
            if stop_event is None:
                return await run_task

            stop_task = asyncio.create_task(stop_event.wait())
            try:
                done, _pending = await asyncio.wait(
                    {run_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done and not run_task.done():
                    app.exit(result=None)
                return await run_task
            finally:
                stop_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stop_task

        try:
            return await _run_until_stopped()
        finally:
            if remove_queue_callback is not None:
                remove_queue_callback()

    def confirm_action(self, message: str) -> bool:
        """Ask for confirmation with yes/no prompt."""
        try:
            return confirm(message)
        except (EOFError, KeyboardInterrupt):
            return False

    def show_command_help(self) -> None:
        """Display available commands in a formatted way."""
        console.print("\n[bold cyan]Available Slash Commands:[/bold cyan]")
        for command, description in self.commands.items():
            console.print(f"  [cyan]/{command}[/cyan] - {description}")
        console.print()

    def update_session(self, session_id: str) -> None:
        """Update the session ID displayed in the status line."""
        if self.status_line:
            self.status_line.update_session(session_id)

    def reset_history(self) -> None:
        """Reset interactive input history for a fresh session."""
        self.history = InMemoryHistory()
        self.auto_suggest.reset_history()

    def set_next_input_text(self, text: str) -> None:
        """Prefill the next prompt buffer with restored user input."""
        self._next_input_text = text

    def set_vim_mode(self, enabled: bool) -> None:
        """Set vim mode state and persist it."""
        if enabled:
            self.vim_mode_manager.enable()
        else:
            self.vim_mode_manager.disable()
        self.vim_mode_manager.save()

    def toggle_vim_mode(self) -> bool:
        """Toggle vim mode and return new state."""
        self.vim_mode_manager.toggle()
        self.vim_mode_manager.save()
        return self.vim_mode_manager.enabled
