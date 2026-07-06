---
name: update-config
description: Update Koder configuration - settings.json permissions, hooks, statusLine, sandbox, session env via /env, process env vars, or config.yaml model settings. Use when the user asks to change a setting, allow a command, add a hook, set an automated behavior ("whenever X, do Y"), or troubleshoot why a hook or permission is not taking effect.
argument_hint: "<setting to change>"
---

Update Koder configuration for: $ARGUMENTS

## The capability boundary: events need hooks, not memory

If the user wants something to happen automatically in response to an EVENT
("whenever X", "every time X", "before/after X"), that requires a **hook** in a
settings.json file. The harness executes hooks; memory and preferences cannot
trigger automated actions — a note saying "always run prettier after edits"
does nothing at runtime. Map the request to a hook first:

- "After every file edit, run the formatter" → `PostToolUse` hook with matcher `edit_file|write_file`
- "Before compaction, remind me to save state" → `PreCompact` hook
- "Log every shell command" → `PreToolUse` hook with matcher `run_shell`

Hook event names must be one of the `HOOK_EVENTS` values in
`koder_agent/harness/hooks/runtime.py`; read that set before using an uncommon
or newly added event. Common events include `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`,
`PreCompact`, `PostCompact`, `PermissionRequest`, `PermissionDenied`,
`ConfigChange`, `CwdChanged`, `InstructionsLoaded`, and `FileChanged`. Matchers
match Koder tool names (e.g. `run_shell`, `edit_file`); `*` or an empty matcher
matches everything. Only requests about the user's standing preferences with no
triggering event ("I prefer tabs") belong in memory instead.

## Koder's real configuration surface

| File/command | Format | Holds | Scope |
|---|---|---|---|
| `~/.koder/config.yaml` | YAML | model (name, provider, api_key, base_url, reasoning_effort, small_model), cli defaults, mcp_servers, skills dirs, voice, harness settings | user |
| `~/.koder/settings.json` | JSON | hooks, permissions, statusLine, sandbox, disableAllHooks | user, all projects |
| `.koder/settings.json` | JSON | same keys, committed to git | project, team-wide |
| `.koder/settings.local.json` | JSON | same keys, gitignored | project, this machine only |
| `/env` / `~/.koder/session-env/<session-id>.sh` | shell exports | session-scoped environment variables for the active session and child commands | session |
| `~/.koder/managed-settings.json` | JSON | managed hooks and sandbox/disableAllHooks policy | local policy, inspect only |

For `config.yaml`-backed values, precedence is **CLI arguments > environment
variables > config file > defaults**. Key environment variables include
`KODER_API_KEY` (universal, overrides provider keys), `KODER_BASE_URL`,
`KODER_MODEL`, `KODER_REASONING_EFFORT` (`none`/`minimal`/`low`/`medium`/`high`),
and `KODER_SMALL_MODEL`. If a config.yaml change "does not take effect", check
whether an env var or CLI flag is shadowing it before editing anything.

Settings files are not one global precedence chain. Permission rules live under
`permissions.allow` / `permissions.deny` as `"tool_name(content)"` strings, e.g.
`"run_shell(npm *)"`; matching deny rules are evaluated before matching allow
rules. Hooks, sandbox, and statusLine each have their own runtime loader, and
managed settings can disable or lock parts of the policy. Inspect
`/hooks` or `/sandbox status` when troubleshooting.
Hook config shape:

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "edit_file|write_file",
        "hooks": [ { "type": "command", "command": "npx prettier --write ." } ] }
    ]
  }
}
```

## Ground truth: read the schema, do not guess keys

The authoritative schema for `config.yaml` is
`koder_agent/config/models.py` (`KoderConfig`) extended by
`koder_agent/harness/config/schema.py` (`RuntimeConfig`, including the
`harness:` section). Hook events and payloads are defined in
`koder_agent/harness/hooks/runtime.py`. If those paths are not present in the
working directory (Koder is installed as a package), locate the installed
sources first via run_shell:
`python3 -c "import koder_agent.config.models as m; print(m.__file__)"`
and read from the printed path. When unsure whether a key exists or
what values it accepts, read those files with read_file — never invent a key
name from another tool's config format. Unknown `config.yaml` and settings keys are
ignored by the current loaders unless runtime code explicitly reads them; invalid values for
known schema keys can raise validation errors.

## CRITICAL: read before write, merge never replace

1. **Read the target file first** with read_file. If it does not exist,
   confirm with the user before creating it.
2. **Merge** your change into the existing structure. Never write a file that
   contains only your new key — that erases the user's existing hooks,
   permissions, and settings. When adding to an array (`permissions.allow`,
   a hook event's list), append to the existing entries; do not replace the
   array.
3. Prefer edit_file for surgical changes to an existing file; use write_file
   only for a file that does not exist yet.
4. Preserve the file's formatting: valid JSON (no comments, no trailing
   commas) for settings files, valid YAML for config.yaml.

## Choose the scope deliberately

- Personal preference across all projects → `~/.koder/settings.json` or `~/.koder/config.yaml`
- Team convention this repo should enforce → `.koder/settings.json` (committed)
- Personal override for this repo only → `.koder/settings.local.json` (gitignored)

If the user did not say which scope they want and the choice changes behavior
(e.g. a permission the whole team would inherit), ask one short question
instead of guessing.

## Workflow

1. Classify the request: event-driven automation (hook), permission,
   session-scoped env (`/env`), process env var, model/provider setting,
   sandbox, or display/statusline.
2. Pick the target file and scope; verify key names against the schema.
3. read_file the current contents; plan the minimal merged change.
4. Apply with edit_file (or write_file for a new file).
5. Re-read or lint the result: JSON must parse (`python3 -c "import json,sys; json.load(open('...'))"`
   via run_shell is a cheap check), YAML must load.
6. Confirm to the user: which file changed, the exact key(s) added or
   modified, and whether a restart, new session, or command-specific reload is
   needed. Model/provider config and permission hierarchy are generally resolved
   at session/runtime setup; hooks, sandbox, and statusLine are read by their
   runtime helpers when invoked; `/env` applies to the active session.

Anti-patterns: writing a whole new settings.json around one key; putting hooks
in config.yaml; putting model/API keys or session env vars in settings.json;
"configuring" an automated behavior by writing it into memory; editing managed
policy files.
