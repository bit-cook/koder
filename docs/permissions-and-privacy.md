# Permissions And Privacy

Koder is a local terminal assistant. It can read and edit files, run shell commands, call configured model providers, and connect to configured local extensions. This guide explains how to inspect and control those boundaries.

## Local Data Paths

Default paths:

| Path | Purpose |
|---|---|
| `~/.koder/config.yaml` | User configuration. |
| `~/.koder/settings.json` | User permission and runtime settings. |
| `.koder/settings.json` | Project permission and runtime settings. |
| `~/.koder/koder.db` | Sessions and transcripts. |
| `~/.koder/tokens/` | OAuth token stores and model caches. |
| `~/.koder/agents/`, `~/.koder/teams/`, `~/.koder/tasks/` | Agent, team, and task state. |
| `.koder/memory/`, `~/.koder/memory/` | Project and user memory files. |

Koder does not upload sessions to a Koder-hosted service. Model requests still go to the configured model provider.

## Permission Commands

Inspect the active policy:

```bash
/permissions
/sandbox
/sandbox status
/privacy-settings
```

Change sandbox policy:

```bash
/sandbox enable
/sandbox enable unix-local
/sandbox disable
```

The permission layer protects shell, file, tool, and teammate operations. Foreground `run_shell` commands can also run through a real OpenAI Agents SDK sandbox backend when sandbox policy is enabled and the configured backend is available. `/sandbox status` reports the active backend and whether it is available.

Sandbox policy supports these high-level modes:

| Mode | Behavior |
|---|---|
| `read-only` | Mutating shell commands are denied before execution. |
| `workspace-write` | Foreground shell commands run in the selected backend and can write the workspace while outside-workspace writes are blocked by supported backends. |
| `danger-full-access` | Sandbox disabled; shell commands use the normal local executor. |

Koder currently treats file tools, MCP servers, teammate processes, and background shell commands as permission-backed unless their execution path is explicitly routed through a sandbox backend. Hosted backends are listed with missing dependency or credential hints; credentials are not printed. See the [Sandbox Guide](sandbox.md) for setup, status fields, backend options, and troubleshooting.

## Workspace Directories

Koder starts from the current working directory. Add another workspace root only when a task needs it:

```bash
/add-dir /path/to/other/workspace
```

Use `/files`, `/context`, and `/ctx_viz` to see what the session has loaded.

## Managed Settings

Managed settings are local high-priority policy files:

```text
~/.koder/managed-settings.json
```

Inspect them with:

```bash
/managed-settings
/hooks
/sandbox status
```

Koder does not fetch a hosted managed-settings service. The command reports the local policy file currently present on disk.

## Shell Commands

Shell commands can be run from the TUI with `!` or by the model through shell tools. Mutating commands may require approval depending on policy.

Examples:

```bash
!git status --short
!uv run pytest tests/test_file_tools.py
```

Background commands can be started with `&` and monitored or stopped by shell tooling.

## Secrets

Prefer environment variables for secrets:

```bash
export KODER_API_KEY="sk-..."
export KODER_BASE_URL="https://your-endpoint.example/v1"
```

Avoid putting API keys in project files. OAuth tokens are stored under `~/.koder/tokens/` and refreshed locally by provider-specific auth flows.

## Privacy Checks

Use these commands when you want to verify what Koder can see:

```bash
/privacy-settings
/status
/files
/context
/memory
/agents summary
/tasks
```

Settings bundles can move local settings and memory between machines, but they intentionally exclude token stores, model caches, transcripts, task records, and plugin caches:

```bash
koder config export ~/koder-settings.json
koder config import ~/koder-settings.json --dry-run
```
