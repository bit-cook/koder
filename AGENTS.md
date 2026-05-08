# AGENTS.md

This file provides guidance to Koder and other AI agents when working with code in this repository.

## Commands

### Development Setup

```bash
uv sync                                    # Install dependencies
uv run koder                               # Run in interactive mode
uv run koder "Your prompt"                 # Single prompt
uv run koder -s my-session "Your prompt"   # Named session
uv run koder --resume                      # Resume previous session
```

### Code Quality

```bash
uv run black .                                          # Format
uv run ruff format                                      # Format imports/style
uv run ruff check --fix                                 # Lint with auto-fix
uv run pylint koder_agent/ --disable=C,R,W --errors-only # Error-only check
```

### Testing

```bash
uv run pytest                          # All tests
uv run pytest tests/test_file_tools.py # Single file
uv run pytest -v -k "test_name"        # Single test by name
```

### MCP Server Management

```bash
uv run koder mcp list                              # List servers
uv run koder mcp add myserver "python -m server"   # Add server
uv run koder mcp remove myserver                   # Remove server
```

## Architecture

Koder is a terminal-based AI coding assistant built on the `openai-agents` library with multi-provider support via LiteLLM.

### Package Structure

```md
koder_agent/
├── agentic/        # Agent creation, hooks, guardrails, approval system
├── auth/           # OAuth providers, token storage, provider-specific tool conversion
├── cli.py          # Main CLI entry point
├── config/         # Configuration management (YAML, env vars)
├── core/           # Scheduler, sessions, streaming, security, interactive prompt
├── harness/        # Runtime commands, plugins, memory, permissions, teams, UI scaffolding
├── mcp/            # Model Context Protocol server integration
├── providers/      # Provider routing metadata
├── tools/          # Tool implementations
└── utils/          # Client setup, prompts, sessions, model info, terminal theme
```

### Core Flow

1. **CLI Entry** (`cli.py`) parses args and creates a runtime request.
2. **HarnessRuntime** (`harness/runtime.py`) loads permissions and dispatches interactive, prompt, and subcommand modes.
3. **Session Flow** (`harness/session_flow.py`) wires context, hooks, plugins, agents, slash commands, and scheduler execution.
4. **AgentScheduler** (`core/scheduler.py`) orchestrates execution with streaming and usage tracking.
5. **Agent Creation** (`agentic/agent.py`) builds the agent with tools, MCP servers, model settings, and provider routing.
6. **Tool Engine** (`tools/engine.py`) registers tools, validates inputs, and filters sensitive output.
7. **Session Storage** (`core/session.py`) persists conversations in SQLite with token-aware compression helpers.

### Key Design Patterns

- **Provider Abstraction**: `utils/client.py` detects providers from environment/config and uses native OpenAI clients or LiteLLM wrappers as appropriate.
- **OAuth Providers**: `auth/providers/` supports Google, Claude, ChatGPT, and Antigravity subscription-backed model access while keeping tokens under `~/.koder/tokens/`.
- **RetryingLitellmModel**: `agentic/agent.py` wraps LiteLLM calls with retry behavior for rate limits and transient errors.
- **Progressive Disclosure Skills**: `tools/skill.py` loads skill metadata at startup and full content on demand.
- **Skill Restrictions**: `tools/skill_context.py` and `agentic/skill_guardrail.py` limit tool access when restricted skills are active.
- **Streaming Display**: `core/streaming_display.py` manages Rich live displays for real-time output.
- **Approval Hooks**: `agentic/approval_hooks.py` and `harness/permissions/` wrap tool execution with permission checks.
- **Security Guard**: `core/security.py` and `core/bash_security.py` validate shell commands before execution.
- **Background Shells**: `tools/shell.py` tracks async shell commands.
- **Agent Teams**: `harness/agents/teams/` supports in-process and tmux-backed teammate execution.

### Tool Categories

| Category | Tools |
|----------|-------|
| File | `read_file`, `write_file`, `append_file`, `edit_file`, `list_directory`, `notebook_edit` |
| Search | `glob_search`, `grep_search` |
| Shell | `run_shell`, `shell_output`, `shell_kill`, `git_command` |
| Web | `web_search`, `web_fetch` |
| Task | `task_delegate`, `todo_read`, `todo_write`, task lifecycle tools |
| Skills | `get_skill`, bundled skills, plugin skills |
| Runtime | config, MCP resource, worktree, plan mode, ask-user, team messaging |

### Configuration Priority

CLI Arguments > Environment Variables > Config File (`~/.koder/config.yaml`) > Defaults

Key environment variables:

- `KODER_API_KEY` - Universal API key, overrides provider-specific keys.
- `KODER_BASE_URL` - Custom API endpoint, overrides provider-specific base URLs.
- `KODER_MODEL` - Model name, for example `gpt-4o`, `claude-opus-4-20250514`, or `github_copilot/gpt-5.1-codex`.
- `KODER_REASONING_EFFORT` - Reasoning effort for reasoning models (`low`, `medium`, `high`).
- Provider API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GITHUB_TOKEN`, and related provider variables.

### Database

SQLite at `~/.koder/koder.db` stores conversation history, session metadata, and MCP server configuration.

### Project Context

The CLI loads `AGENTS.md` from the working directory as project-specific context for the agent.

### Skills System

Skills are loaded from `.koder/skills/` (project) and `~/.koder/skills/` (user). Each skill has a `SKILL.md` with YAML frontmatter defining `name`, `description`, and optional `allowed_tools`.

## Requirements

- Always run `uv run black . && uv run ruff format && uv run ruff check --fix` and fix warnings/errors whenever code changes are made.
- Always use `uv run` whenever you need to run, evaluate, or test Python scripts.
- Before claiming TUI behavior, validate the real terminal flow with scenario-based tmux checks from `tests/e2e/tui_feature_scenarios.json`. Validate scenarios with `uv run scripts/tmux_feature_scenarios.py --check`; run focused scenarios with `uv run scripts/tmux_feature_scenarios.py --run <name>` or the full suite with `--run-all`.
