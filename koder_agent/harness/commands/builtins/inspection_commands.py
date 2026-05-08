"""Inspection and support command descriptors."""

INSPECTION_COMMAND_SPECS = {
    "files": {"help_text": "Inspect workspace files"},
    "diff": {"help_text": "Show pending diffs"},
    "context": {"help_text": "Inspect injected context"},
    "cost": {"help_text": "Show usage cost information"},
    "doctor": {"help_text": "Run environment diagnostics"},
    "ide": {"help_text": "Inspect local IDE launchers"},
    "help": {"help_text": "Show help for commands", "aliases": ("?",)},
    "copy": {"help_text": "Copy selected output"},
    "export": {"help_text": "Export session artifacts"},
}
