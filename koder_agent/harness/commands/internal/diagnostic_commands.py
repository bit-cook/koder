"""Diagnostic and admin command descriptors."""

DIAGNOSTIC_COMMAND_SPECS = {
    "bughunter": {"help_text": "Run local bug-triage diagnostics"},
    "debug-tool-call": {"help_text": "Inspect recorded tool-call context"},
    "heapdump": {"help_text": "Write local Python heap diagnostics"},
    "issue": {"help_text": "Inspect internal issue metadata"},
    "version": {"help_text": "Show detailed runtime version info"},
    "summary": {"help_text": "Emit internal program summary data"},
    "env": {"help_text": "Inspect selected environment state"},
    "rate-limit-options": {"help_text": "Inspect provider limit handling options"},
    "oauth-refresh": {"help_text": "Force an OAuth refresh diagnostic"},
}
