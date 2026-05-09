# Koder Command Reference

Generated: `2026-05-08T01:57:57+00:00`

This reference lists the live Koder slash-command registry with usage examples and concise descriptions. For task-oriented guidance, start with the topic guides in [Feature Guide](features.md).

## Summary

- Runtime slash commands: `93`
- Common workflow rows: `16`
- Missing runtime command entries: `0`

## Slash Commands

| Command | Usage Example | Typical Use | Steps | Description |
|---|---|---|---|---|
| `/add-dir` | `/add-dir /tmp` | Validate workspace directory admission, permissions visibility, non-directory guidance, and missing-path errors. | `4` | Add a workspace directory to the active session |
| `/advisor` | `/advisor local fixture` | Run an advisor review through a deterministic OpenAI-compatible fixture with session transcript, focus, git diff, advisor model routing, and no-context short-circuit coverage. | `8` | Run an advisor review over session and git context |
| `/agents` | `/agents` | List project agents, report malformed files, create and inspect a project agent, verify runtime summary output, and delete the agent. | `9` | List, inspect, create, and manage local agents |
| `/assistant` | `/assistant` | Inspect active assistant status, list agent-backed profiles, show project profile metadata, and handle usage/not-found edges. | `5` | Inspect active Koder assistant profiles |
| `/autofix-pr` | `/autofix-pr` | Validate autofix command usage and gh-backed PR diff handling without mutating a PR or workspace files. | `7` | Internal autofix pull-request workflow |
| `/backfill-sessions` | `/backfill-sessions` | Migrate legacy ctx sessions, list migrated metadata, and prove the command is idempotent. | `5` | Backfill legacy session metadata |
| `/branch` | `/branch` | Inspect dirty git branch state, reject invalid names, and create a new branch from the TUI. | `5` | Inspect or create working branches |
| `/brief` | `/brief` | Toggle brief-only mode on and off while proving config.yaml persistence. | `5` | Toggle brief-only assistant responses |
| `/btw` | `/btw` | Ask a side question through a deterministic OpenAI-compatible LLM fixture using recent session context. | `6` | Ask a quick side question using session context |
| `/buddy` | `/buddy status` | Inspect companion empty state, hatch and persist a deterministic companion, pet it, and mute/unmute it. | `10` | Manage the local buddy companion state |
| `/bughunter` | `!git add AGENTS.md docs/runtime-notes.md .koder sample.txt && git commit -m bughunter-clean-baseline && printf '%s\n' 'def ratio(numerator, denominator):' '    return numerator / denominator' > bughunter_target.py && git add bughunter_target.py && git commit -m bughunter-target-baseline && printf '%s\n' 'def ratio(numerator, denominator):' '    return numerator / 0' > bughunter_target.py && echo bughunter-fixture` | Run local bug-triage diagnostics over workspace status, diff stat, diff evidence, and clean-repo state. | `5` | Run local bug-triage diagnostics |
| `/channels` | `/channels` | Launch Koder with server, plugin, and development channel entries, then verify /channels state and CLI UX. | `4` | Inspect configured MCP/plugin channels |
| `/clear` | `/rename clear-source` | Clear current workflow state, reset prompt history, switch to a fresh session, and keep the previous named session resumable. | `10` | Clear current workflow state |
| `/color` | `/color` | Set, display, persist, reject invalid, and reset the active session color. | `7` | Set the current session color |
| `/commit` | `!git add AGENTS.md docs/runtime-notes.md .koder && git commit -m fixture-baseline && printf 'staged\n' > staged.txt && git add staged.txt && printf 'more unstaged\n' >> sample.txt && printf 'untracked\n' > untracked.txt && echo commit-fixture` | Inspect commit readiness across staged, unstaged, untracked, and clean git states without creating a commit through Koder itself. | `5` | Inspect git commit readiness |
| `/commit-push-pr` | `/commit-push-pr` | Inspect branch, remote, status, staged diff, unstaged diff, and clean readiness without pushing or opening a PR. | `4` | Inspect branch, diff, and PR publish readiness |
| `/compact` | `!uv --project "$PYTHONPATH" run --no-sync python -c 'import asyncio, sqlite3; from pathlib import Path; from koder_agent.core.session import EnhancedSQLiteSession; db=Path.home()/".koder"/"koder.db"; conn=sqlite3.connect(db); sid=conn.execute("select session_id from session_metadata order by updated_at desc limit 1").fetchone()[0]; items=[{"role":"user","content":"compact older user"},{"role":"assistant","content":"compact older assistant"},{"role":"user","content":"compact kept user"},{"role":"assistant","content":"compact kept assistant"}]; asyncio.run(EnhancedSQLiteSession(sid).add_items(items)); marker="compact-"+"fixture"; print(marker, sid)'` | Seed session history, run manual compaction, prove persisted compacted transcript shape, and keep the TUI usable. | `5` | Compact the active conversation state |
| `/config` | `/config` | Inspect runtime configuration after live model and reasoning-effort changes, including persisted YAML state. | `7` | Inspect and update runtime configuration |
| `/context` | `/context` | Render deterministic context-token categories for instructions, seeded conversation, and seeded files, with /ctx_viz cross-checks. | `4` | Inspect injected context |
| `/copy` | `/copy` | Copy seeded assistant responses by default and explicit index through a stub clipboard command while preserving empty and invalid UX. | `8` | Copy selected output |
| `/cost` | `/cost` | Display zero, known-model, and unknown-model session cost data from durable usage snapshots. | `6` | Show usage cost information |
| `/ctx_viz` | `/ctx_viz` | Render project context, exact session message counts, seeded session files, recent transcript lines, and /context cross-checks. | `4` | Render loaded context and recent transcript details |
| `/debug-tool-call` | `/debug-tool-call` | Inspect empty and seeded tool-call debug records with redacted argument and output previews plus invalid selection handling. | `8` | Inspect recorded tool-call context |
| `/diff` | `!uv --project "$PYTHONPATH" run --no-sync python -c 'import asyncio,sqlite3; from pathlib import Path; from koder_agent.core.session import EnhancedSQLiteSession; db=Path.home()/".koder"/"koder.db"; conn=sqlite3.connect(db); sid=conn.execute("select session_id from session_metadata order by updated_at desc limit 1").fetchone()[0]; items=[{"role":"user","content":"change conversation file"},{"toolUseResult":{"filePath":"conversation.txt","structuredPatch":[{"lines":["-old","+new","+extra"]}]}}]; asyncio.run(EnhancedSQLiteSession(sid).add_items(items)); Path("diff-seed.txt").write_text("seeded conversation diff\n", encoding="utf-8"); print("diff-fixture", sid)'` | Report git diff state and seeded conversation edit summaries in one TUI flow, including a clean-git edge. | `5` | Show pending diffs |
| `/doctor` | `/doctor` | Run local environment diagnostics, assert healthy runtime fields, and cross-check the same TUI session with shell, version, and status output. | `4` | Run environment diagnostics |
| `/effort` | `/effort high` | Inspect, set, persist, inherit, reject invalid, and reset reasoning effort through the TUI. | `7` | Show or change reasoning effort |
| `/reasoning` | `/reasoning summary` | Inspect, set, persist, reject invalid, and disable reasoning display through the TUI. | `8` | Show or change reasoning display |
| `/env` | `/env` | Inspect, persist, unset, reject invalid, and clear session-scoped environment variables through the TUI. | `7` | Inspect selected environment state |
| `/exit` | `/exit` | Exit the interactive session and prove the tmux-backed TUI process terminates cleanly. | `3` | Exit the active workflow |
| `/export` | `/export` | Export seeded session metadata and transcript content to screen, JSON, and Markdown while handling invalid paths. | `9` | Export session artifacts |
| `/feedback` | `/feedback` | Capture a redacted feedback event with repository context and durable local storage. | `3` | Capture feedback with repository context |
| `/files` | `/files` | Report seeded session context files with exact relative paths, de-duplication, and exists/missing status. | `6` | Inspect workspace files |
| `/fork` | `/fork` | Launch background subagents with usage guidance, isolated default context, explicit fork context, inherited model/base URL/reasoning config, and redacted runtime records. | `4` | Launch or resume a background subagent |
| `/heapdump` | `/heapdump` | Write a local Python heap diagnostic JSON file, inspect its required fields and privacy boundary, and continue using the same TUI session. | `3` | Write local Python heap diagnostics |
| `/help` | `/help` | Display the full command catalog with real descriptions, command-specific help, and unknown-command guidance. | `5` | Show help for commands |
| `/hooks` | `/hooks` | List project-configured hook events, matchers, and commands from the real TUI. | `2` | List configured runtime hooks |
| `/ide` | `/ide` | Detect a stubbed local IDE launcher, validate status and edge UX, route open commands to the stub, and assert no real app launch is needed. | `7` | Inspect local IDE launchers |
| `/init` | `/init` | Generate a local AGENTS.md guide when missing, report Magic Docs, and refuse overwriting existing files. | `6` | Generate an AGENTS.md project guide |
| `/init-verifiers` | `/init-verifiers cli` | Create a project verifier skill, inspect its generated SKILL.md contract, prove /skills discovers it, and rerun the idempotent exists path. | `5` | Create project verifier skills |
| `/insights` | `/insights` | Display current session analytics for transcript items, role counts, tool activity, context files, and usage counters. | `4` | Show current session analytics |
| `/install` | `/install` | Inspect local Koder development installation status, command paths, usage guidance, and backing project files. | `4` | Inspect local Koder installation status |
| `/install-github-app` | `/install-github-app` | Inspect, plan, and apply Koder GitHub Actions setup through a deterministic gh fixture without pushing from git. | `5` | Set up Koder GitHub Actions workflows |
| `/issue` | `/issue` | List GitHub issues through a deterministic gh fixture, show creation guidance, and report empty/failure edges accurately. | `7` | Inspect internal issue metadata |
| `/keybindings` | `/keybindings` | List, persist, reject invalid, and re-read interactive keybindings through the TUI. | `5` | Inspect and update interactive keybindings |
| `/magic-docs` | `/magic-docs` | List Magic Docs, refresh the managed section, verify file content, remove the header, and prove the tracked record is cleaned up. | `8` | Inspect or refresh tracked Magic Docs |
| `/managed-settings` | `/managed-settings` | Inspect managed policy file details and prove sandbox policy lock reaches runtime sandbox status. | `4` | Inspect managed runtime policy settings |
| `/mcp` | `/mcp` | Round-trip project MCP configuration through the CLI, TUI listing, diagnostics, and cleanup. | `8` | Inspect MCP integration settings |
| `/memory` | `/memory` | Inspect empty, seeded project/user, index-ignored, and /remember-created memory files through the real TUI. | `5` | Inspect stored runtime memories |
| `/model` | `/model gpt-4o` | Change the active model/provider, prove status and subagent inheritance use it, then reset to the default OpenAI model. | `6` | Show or change the active model |
| `/oauth-refresh` | `/oauth-refresh` | Inspect local OAuth token-store diagnostics for empty, valid, expired, malformed, and usage states without network refresh. | `4` | Force an OAuth refresh diagnostic |
| `/onboarding` | `/onboarding` | Validate onboarding checklist against real API-key, model, and workspace state transitions. | `8` | Show local setup checklist status |
| `/output-style` | `/output-style` | Inspect, update, and reset terminal output style controls across theme, color, vim, statusline, and persisted settings. | `8` | Inspect and update terminal output styling |
| `/passes` | `/passes` | Show verification status from real pytest cache, including no-cache, passing, and failing test states. | `5` | Show verification passes |
| `/peers` | `/peers create peers-scenario` | Create and inspect an agent team, consume mailbox work, record history, and complete a persisted team task. | `11` | Create and manage local agent teams |
| `/permissions` | `/permissions` | Validate permission status, mutating shell approvals, sandbox auto-allow when the configured backend is available, excluded commands, and persisted settings. | `6` | Inspect runtime permission behavior |
| `/plan` | `/plan` | Enter and exit plan mode while proving read-only permission enforcement through the TUI. | `6` | Enter read-only plan mode |
| `/plugin` | `/plugin` | List the installed plugin, reject a missing plugin toggle, disable and re-enable the fixture plugin, and assert persisted enabled state. | `6` | Inspect and manage installed plugins |
| `/pr-comments` | `/pr-comments` | Fetch and render PR comments through a deterministic local gh fixture, including threaded review comments and failure edges. | `8` | Render GitHub PR comments for the current branch |
| `/pr_comments` | `/pr_comments` | Validate that the underscore alias shares the same deterministic gh-backed renderer as /pr-comments. | `4` | Render GitHub PR comments for the current branch |
| `/privacy-settings` | `/privacy-settings` | Show local privacy paths, secret redaction policy, and local memory storage behavior. | `4` | Show local privacy and data-storage settings |
| `/rate-limit-options` | `/rate-limit-options` | Inspect provider limit guidance, retryable error categories, live model/provider state, and zero-usage counter agreement. | `4` | Inspect provider limit handling options |
| `/release-notes` | `/release-notes` | Seed cached changelog state before launch, render filtered release notes, persist last-seen version, and cross-check runtime version. | `3` | Show recent Koder release notes or commits |
| `/reload-plugins` | `/reload-plugins` | Reload plugin registry after adding and removing a plugin during the TUI session, proving plugin and skill visibility update. | `9` | Reload installed plugins |
| `/rename` | `/rename` | Rename the active session, verify visible session metadata, and assert SQLite persistence. | `3` | Rename the active session |
| `/resume` | `/resume resume-title` | Resume existing sessions by exact title and id while rejecting missing and ambiguous targets. | `7` | Resume a previous session |
| `/review` | `/review` | Review local and PR diffs through deterministic fake gh and OpenAI-compatible fixtures, including clean and gh-failure edges. | `8` | Start a review-oriented workflow |
| `/rewind` | `!uv --project "$PYTHONPATH" run --no-sync python -c 'import asyncio, sqlite3; from pathlib import Path; from koder_agent.core.session import EnhancedSQLiteSession; db=Path.home()/".koder"/"koder.db"; conn=sqlite3.connect(db); sid=conn.execute("select session_id from session_metadata order by updated_at desc limit 1").fetchone()[0]; items=[{"role":"user","content":"first prompt"},{"role":"assistant","content":"first reply"},{"role":"user","content":"second prompt"},{"role":"assistant","content":"second reply"}]; asyncio.run(EnhancedSQLiteSession(sid).add_items(items)); marker="rewind-"+"seed"; print(marker, sid)'` | Seed conversation history, list rewind targets, restore a selected prompt into input, and prove session history is trimmed. | `9` | Rewind recent workflow state |
| `/sandbox` | `/sandbox status` | Validate sandbox status, backend choices, enable, disable, excluded command patterns, and persisted local settings. | `5` | Inspect and configure sandbox settings |
| `/schedule` | `/schedule` | Inspect the scheduled task registry from real cron storage, including empty, created, deleted, and malformed states. | `8` | Inspect scheduled task registry |
| `/security-review` | `/security-review` | Run a deterministic security review over pending git changes and verify the clean-worktree short circuit. | `5` | Run a security-focused review of pending changes |
| `/session` | `/session` | Inspect active session identifiers and metadata before and after title/tag mutations. | `4` | Inspect the current session |
| `/share` | `/rename share-title` | Render share metadata after title, tag, and color changes without exporting transcript content. | `5` | Show share metadata for the active session |
| `/skills` | `/skills` | List project, user, and plugin skills with precedence, then verify plugin disable/reload hides and restores plugin skills. | `9` | Inspect available skills |
| `/stats` | `/stats` | Show local database and current-session statistics with exact fresh-DB, seeded-history, and zero-usage edges. | `4` | Show session statistics |
| `/status` | `/status` | Show live runtime status for version, effective model, provider, session id, command count, connectivity, and cwd after model changes. | `5` | Show runtime status |
| `/statusline` | `/statusline clear` | Import a shell prompt into statusline settings, verify output-style resolution, clear it, and assert persisted settings are clean. | `5` | Configure or clear the terminal status line |
| `/subscribe-pr` | `/subscribe-pr` | List open pull requests through a deterministic gh fixture and report subscription guidance, empty state, and failure state. | `6` | Inspect open PRs and subscription guidance |
| `/summary` | `/summary` | Summarize current session title, zero usage, git diff stat, and recent commits from deterministic local state. | `2` | Emit internal program summary data |
| `/tag` | `/tag` | Add, display, remove, and persistently clear a session tag. | `5` | Tag the active session |
| `/tasks` | `/tasks` | Inspect runtime task tracker empty state, persisted AutoDream task rows, and malformed task diagnostics. | `3` | Inspect active runtime tasks |
| `/teleport` | `!mkdir -p teleport-target && printf 'not a directory' > teleport-file && echo teleport-fixture` | Reject invalid teleport targets, change cwd to a fixture directory, and persist the new session cwd. | `6` | Change the active workspace directory |
| `/terminal-setup` | `/terminal-setup` | Inspect seeded terminal environment settings, visible related controls, status alias, and invalid argument handling. | `4` | Inspect terminal environment settings |
| `/terminalSetup` | `/terminalSetup` | Validate camelCase terminal setup alias returns canonical terminal setup diagnostics. | `3` | Inspect terminal environment settings |
| `/theme` | `/theme dark` | Set, persist, inspect, reject invalid, and reset terminal theme value. | `6` | Change terminal theme settings |
| `/thinkback` | `/thinkback nope` | Review deterministic local session counts, title, and limited recent prompts without model execution. | `4` | Review recent local session context |
| `/thinkback-play` | `/thinkback-play` | Replay deterministic local session turns with empty-state, invalid-limit, order, and limit checks. | `4` | Replay recent local session turns |
| `/torch` | `/torch` | Generate a provider-backed code exploration plan through a deterministic OpenAI-compatible fixture. | `5` | Explore a codebase topic with a structured plan |
| `/ultraplan` | `/ultraplan` | Generate a provider-backed implementation plan through a deterministic OpenAI-compatible fixture without mutating the workspace. | `6` | Create a comprehensive implementation plan |
| `/upgrade` | `/upgrade` | Inspect local Koder update guidance, verify the helper script help path, and cover invalid usage. | `4` | Inspect local Koder upgrade options |
| `/usage` | `/usage` | Show persisted session token, context, and cost totals and reset them across a new session. | `6` | Show session usage totals |
| `/version` | `/version` | Render detailed runtime version information and compare it with status and CLI version output. | `3` | Show detailed runtime version info |
| `/vim` | `/vim on` | Toggle vim input mode, prove persistence, reject invalid input, and reset state. | `5` | Toggle vim input mode |
| `/voice` | `/voice status` | Configure voice provider state, enable and disable voice mode with fake credentials, check unsupported provider handling, and assert config persistence without leaking secrets. | `8` | Configure or toggle provider-backed voice mode |

## Common Interactive Workflows

| Suite | Scenario | Primary Commands | Purpose | Turns |
|---|---|---|---|---|
| `agents` | `background-fork` | /fork, /agents | Launch background subagents with isolated default context, explicit fork context, inherited model/base URL/reasoning config, and redacted persisted runtime records. | `3` |
| `agents` | `project-agent-detail` | /agents | Load project agent metadata, report malformed agent files, create and inspect a project agent, delete it, and assert the backing file is removed. | `8` |
| `teams` | `mailbox-and-task` | /peers | Create a team, route a mailbox message, consume it, handle an empty inbox edge, and complete a persisted team task across turns. | `10` |
| `teams` | `team-memory` | /peers | Create a team, write project-local team memory, sync it into runtime memory, and verify a tmux teammate can read the synced content. | `5` |
| `teams` | `tmux-pane` | /peers | Spawn two tmux-backed teammate panes, preserve completed pane output, verify inherited model config, inspect member state, kill one pane, and verify the missing-pane UX. | `6` |
| `teams` | `tmux-discussion` | /peers | Spawn multiple tmux-backed teammates that send attributed proposals to the lead and verify the discussion history through the CLI and persisted team store. | `6` |
| `teams` | `in-process-discussion` | /peers, /agents | Spawn multiple in-process teammates, route mailbox work to them, collect attributed proposals and a coordinator summary, and verify persisted discussion history. | `15` |
| `skills` | `manual-skill-command` | /demo-skill, /skills | Invoke a project manual-only skill and prove it renders local content without model execution. | `2` |
| `skills` | `project-and-plugin-skill-listing` | /skills, /plugin, /reload-plugins, /demo-plugin:plugin-skill | Verify project and plugin skills are listed with namespaces, plugin skills execute locally, disabling hides and blocks them, and re-enabling restores them. | `9` |
| `features` | `auto-dream-task` | /tasks | Run a deterministic AutoDream consolidation, verify task and memory persistence, update task metadata, and report malformed task records through /tasks. | `4` |
| `features` | `memory-and-session` | /memory, /remember, /rename, /clear, /resume, /session | Save project memory, verify memory listing, rename the active session, switch away, resume by title, and confirm durable session metadata. | `9` |
| `features` | `permissions-and-sandbox` | /permissions, /sandbox | Exercise sandbox policy changes and permission-engine decisions for approval-required, sandboxed, and excluded shell tool calls. | `7` |
| `features` | `prompt-suggestion` | shell mode, /clear | Generate a post-turn ghost suggestion, accept it into the prompt, clear it, and verify stale suggestions do not survive a session clear. | `7` |
| `features` | `settings-bundle` | /config | Export and import local settings bundles, covering dry-run behavior, project scope filtering, token/cache exclusion, restore, and backup UX. | `5` |
| `features` | `slash-completion` | prompt completion, /stats | Filter slash completions in the real prompt, survive a small terminal resize without layout errors, and execute the selected completion. | `3` |
| `features` | `terminal-resize-reflow` | / | Resize after Rich command output and while editing prompt input, verifying visible transcript reflow without layout errors. | `5` |

## Maintainer Verification

Regenerate and verify this reference with:

```bash
uv run scripts/tmux_feature_scenarios.py --check
uv run scripts/tmux_feature_scenarios.py --check --strict-acceptance
```
