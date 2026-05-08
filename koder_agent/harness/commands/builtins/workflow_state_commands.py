"""Workflow state command descriptors."""

WORKFLOW_STATE_COMMAND_SPECS = {
    "assistant": {"help_text": "Inspect active Koder assistant profiles"},
    "init-verifiers": {"help_text": "Create project verifier skills"},
    "memory": {"help_text": "Inspect stored runtime memories"},
    "thinkback": {"help_text": "Review recent local session context"},
    "thinkback-play": {"help_text": "Replay recent local session turns"},
    "tasks": {"help_text": "Inspect active runtime tasks"},
    "tag": {"help_text": "Tag the active session"},
    "clear": {"help_text": "Clear current workflow state"},
    "exit": {"help_text": "Exit the active workflow"},
}
