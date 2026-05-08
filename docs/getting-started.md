# Getting Started

This guide gets Koder from install to a useful first coding session.

## Install

Use `uv tool install` for a clean command-line install:

```bash
uv tool install koder
```

For local development from this repository:

```bash
uv sync
uv run koder
```

## Configure A Model

The shortest setup is a universal API key and a model name:

```bash
export KODER_API_KEY="your-api-key"
export KODER_MODEL="gpt-4o"
koder
```

`KODER_API_KEY`, `KODER_BASE_URL`, `KODER_MODEL`, and `KODER_REASONING_EFFORT` override provider-specific settings and `~/.koder/config.yaml`.

Provider-specific examples:

```bash
OPENAI_API_KEY="sk-..." KODER_MODEL="gpt-4o" koder
ANTHROPIC_API_KEY="..." KODER_MODEL="claude-opus-4-20250514" koder
GOOGLE_API_KEY="..." KODER_MODEL="gemini/gemini-2.5-pro" koder
KODER_BASE_URL="http://localhost:8080/v1" KODER_MODEL="openai/local-model" koder
```

Subscription-backed providers use `koder auth`:

```bash
koder auth login google
koder auth login claude
koder auth login chatgpt
koder auth login antigravity
koder auth list
```

After login, use the provider prefix in `KODER_MODEL`, for example `google/gemini-3-pro-preview`, `claude/claude-opus-4-5-20250514`, or `chatgpt/gpt-5.2`.

See [Configuration Guide](configuration.md) for the full provider matrix.

## Start A Session

Interactive mode is the normal daily workflow:

```bash
koder
```

Single prompt mode is useful from scripts or when you already know the task:

```bash
koder "summarize the current git diff"
```

Named sessions keep a durable conversation attached to a project or topic:

```bash
koder -s billing-refactor
koder -s billing-refactor "continue the failing test investigation"
```

Resume previous work with:

```bash
koder --resume
koder --continue
```

## First Workspace Check

Inside a project, run these commands before asking for large edits:

```bash
/onboarding
/status
/model
/files
/permissions
```

They show the active provider, session, workspace directory, loaded context, and permission policy. If something looks wrong, fix it before delegating substantial work.

## A Safe First Task

Try a read-only task first:

```bash
koder "inspect this repo and explain the test command"
```

Then move to a small edit:

```bash
koder "fix the failing test in tests/test_example.py and run the focused test"
```

Koder will use the project instructions from `AGENTS.md` when present, and it stores session state locally under `~/.koder/`.

## What To Read Next

- [Interactive TUI](interactive-tui.md) for keyboard controls and slash commands.
- [Sessions and Memory](sessions-and-memory.md) for durable context and cleanup.
- [Workflows](workflows.md) for review, planning, Git, and PR workflows.
- [Permissions and Privacy](permissions-and-privacy.md) for local data and tool approval behavior.
