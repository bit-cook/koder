"""Tests for hook event type completeness."""

import pytest

from koder_agent.harness.hooks.runtime import HOOK_EVENTS, HookPayload


class TestHookEventTypes:
    """All 27 hook events should be defined."""

    REQUIRED_EVENTS = [
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
    ]

    @pytest.mark.parametrize("event_name", REQUIRED_EVENTS)
    def test_event_defined(self, event_name):
        assert event_name in HOOK_EVENTS, f"Missing hook event: {event_name}"

    def test_event_count(self):
        assert len(HOOK_EVENTS) == 27


class TestHookPayload:
    """Hook payloads carry the correct data."""

    def test_pre_tool_use_payload(self):
        payload = HookPayload.pre_tool_use(tool_name="Bash", tool_input={"command": "ls"})
        assert payload.event == "PreToolUse"
        assert payload.data["tool_name"] == "Bash"
        assert payload.data["tool_input"]["command"] == "ls"

    def test_post_tool_use_payload(self):
        payload = HookPayload.post_tool_use(
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_output="file.py",
        )
        assert payload.event == "PostToolUse"
        assert payload.data["tool_output"] == "file.py"

    def test_post_tool_use_failure_payload(self):
        payload = HookPayload.post_tool_use_failure(
            tool_name="Bash",
            tool_input={"command": "bad"},
            error="Command failed",
        )
        assert payload.event == "PostToolUseFailure"
        assert payload.data["error"] == "Command failed"

    def test_user_prompt_submit_payload(self):
        payload = HookPayload.user_prompt_submit(prompt="fix the bug", session_id="s123")
        assert payload.event == "UserPromptSubmit"
        assert payload.data["prompt"] == "fix the bug"

    def test_pre_compact_payload(self):
        payload = HookPayload.pre_compact(token_count=50000, threshold=45000)
        assert payload.event == "PreCompact"
        assert payload.data["token_count"] == 50000

    def test_post_compact_payload(self):
        payload = HookPayload.post_compact(old_count=100, new_count=10)
        assert payload.event == "PostCompact"
        assert payload.data["old_message_count"] == 100

    def test_permission_denied_payload(self):
        payload = HookPayload.permission_denied(
            tool_name="Bash",
            tool_input={"command": "rm -rf /"},
            reason="dangerous",
        )
        assert payload.event == "PermissionDenied"
        assert payload.data["reason"] == "dangerous"

    def test_cwd_changed_payload(self):
        payload = HookPayload.cwd_changed(old_cwd="/old", new_cwd="/new")
        assert payload.event == "CwdChanged"
        assert payload.data["old_cwd"] == "/old"
        assert payload.data["new_cwd"] == "/new"

    def test_session_start_payload(self):
        payload = HookPayload.session_start(source="resume", model="gpt-4o")
        assert payload.event == "SessionStart"
        assert payload.data["source"] == "resume"

    def test_stop_payload(self):
        payload = HookPayload.stop(last_assistant_message="Done!")
        assert payload.event == "Stop"
        assert payload.data["last_assistant_message"] == "Done!"

    def test_file_changed_payload(self):
        payload = HookPayload.file_changed(file_path="/tmp/test.py", event="change")
        assert payload.event == "FileChanged"
        assert payload.data["file_path"] == "/tmp/test.py"

    def test_elicitation_payload(self):
        payload = HookPayload.elicitation(server_name="my-mcp", message="Auth required")
        assert payload.event == "Elicitation"
        assert payload.data["server_name"] == "my-mcp"

    def test_payload_is_frozen(self):
        payload = HookPayload.setup()
        with pytest.raises(AttributeError):
            payload.event = "Modified"

    def test_all_events_have_factory(self):
        """Every event in HOOK_EVENTS should have a corresponding factory method."""
        # Map event names to factory method names
        event_to_method = {
            "PreToolUse": "pre_tool_use",
            "PostToolUse": "post_tool_use",
            "PostToolUseFailure": "post_tool_use_failure",
            "Notification": "notification",
            "UserPromptSubmit": "user_prompt_submit",
            "SessionStart": "session_start",
            "SessionEnd": "session_end",
            "Stop": "stop",
            "StopFailure": "stop_failure",
            "SubagentStart": "subagent_start",
            "SubagentStop": "subagent_stop",
            "PreCompact": "pre_compact",
            "PostCompact": "post_compact",
            "PermissionRequest": "permission_request",
            "PermissionDenied": "permission_denied",
            "Setup": "setup",
            "ConfigChange": "config_change",
            "TaskCreated": "task_created",
            "TaskCompleted": "task_completed",
            "TeammateIdle": "teammate_idle",
            "WorktreeCreate": "worktree_create",
            "WorktreeRemove": "worktree_remove",
            "CwdChanged": "cwd_changed",
            "InstructionsLoaded": "instructions_loaded",
            "FileChanged": "file_changed",
            "Elicitation": "elicitation",
            "ElicitationResult": "elicitation_result",
        }
        for event_name, method_name in event_to_method.items():
            assert hasattr(HookPayload, method_name), (
                f"Missing factory method for {event_name}: {method_name}"
            )
