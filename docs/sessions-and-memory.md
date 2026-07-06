# Sessions And Memory

Koder keeps conversation state and local memory so you can resume work without rebuilding context by hand.

## Local Storage

Default storage paths:

| Path | Purpose |
|---|---|
| `~/.koder/koder.db` | SQLite session metadata and transcripts. |
| `~/.koder/koder.db` (`session_goals`) | Durable goal state for each session. |
| `~/.koder/memory/` | User-level memories. |
| `.koder/memory/` | Project-level memories. |
| `.koder/session-memory/` | Project-local session notes. |
| `~/.koder/scheduled_tasks.json` | Cron-backed loop and scheduled prompt records. |
| `~/.koder/tasks/` | Runtime task records, including maintenance tasks. |

Koder does not require a hosted session service for these features.

## Named Sessions

Use named sessions when you want durable work streams:

```bash
koder -s api-migration
koder -s api-migration "continue the serializer cleanup"
koder --resume
koder --continue
```

Inside the TUI:

```bash
/session
/rename api-migration
/resume api-migration
/clear
```

`/clear` switches to a fresh workflow state. The previous named session remains resumable unless you explicitly remove local state outside Koder.

When `/resume` switches sessions inside the TUI, Koder also restores the working directory recorded for that session (and fires the `CwdChanged` hook, see [Hooks](hooks.md)) so the resumed session continues where it actually ran.

## Inspecting Session State

Use these commands to understand what the active session contains:

```bash
/status
/summary
/insights
/usage
/cost
/files
/context
/ctx_viz
```

`/summary` is a compact local status report. `/insights` focuses on transcript roles, tool activity, context files, and usage counters.

## Goals

Use goals when a session has a concrete long-running objective that should persist across turns:

```bash
/goal improve benchmark coverage --budget 50000
/goal
/goal pause
/goal resume
/goal edit improve benchmark and scheduler coverage
/goal budget 75000
/goal clear
```

Goals track objective text, status, elapsed time, token usage, and an optional token budget. Active goals can trigger continuation turns until the goal is completed, paused, blocked, or budget-limited.

## Scheduled Loops

Use local loop jobs for recurring prompts that should run through the active scheduler:

```bash
/loop @every 5m check build
/loop once 30 14 * * 1 monday review
/loop list
/loop delete <id>
/schedule
```

Loop jobs are stored in `~/.koder/scheduled_tasks.json`. `/schedule` is the read-only registry view; `/loop` creates, lists, and deletes jobs.

## Compaction And Rewind

Long sessions can be compacted to keep the useful parts while reducing context size:

```bash
/compact
```

Use rewind when a recent turn sent the session down the wrong path:

```bash
/rewind
```

`/rewind` lists recent prompt targets with the number of newer transcript items each restore would remove, restores the selected prompt into input, and trims later session history.

## Exporting

Use local export commands when you want a durable artifact from a session:

```bash
/export
```

## Memory Commands

Memory files are local markdown files. User memory is shared across projects; project memory stays in the workspace.

```bash
/memory
/remember prefers focused tests before full suites
/thinkback
/thinkback-play
```

`/thinkback` summarizes recent local session context and prompt counts without running a model request. `/thinkback-play` replays recent turns from the active session.

## AutoDream

AutoDream is a best-effort cleanup-time memory consolidation task. When its local cadence threshold is met, it asks the configured provider to extract durable memory notes, writes them to `~/.koder/memory/auto-dream-*.md`, and records task metadata under `~/.koder/tasks/auto-dream/`.

Inspect recent runtime tasks with:

```bash
/tasks
```

## Settings Bundles

Use settings bundles to move configuration and local notes between machines:

```bash
koder config export ~/koder-settings.json
koder config import ~/koder-settings.json --dry-run
koder config import ~/koder-settings.json
```

Bundles include known Koder config, settings, keybindings, user memories, project memories, and project session notes. They exclude token stores, model caches, transcripts, task records, and plugin caches.
