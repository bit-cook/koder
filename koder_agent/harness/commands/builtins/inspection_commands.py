"""Inspection and support command descriptors."""

INSPECTION_COMMAND_SPECS = {
    "files": {"help_text": "Inspect workspace files"},
    "diff": {"help_text": "Show pending diffs"},
    "context": {"help_text": "Inspect injected context"},
    "cost": {"help_text": "Show usage cost information"},
    "doctor": {"help_text": "Run environment diagnostics"},
    "help": {"help_text": "Show help for commands", "aliases": ("?",)},
    "export": {"help_text": "Export session artifacts"},
}
