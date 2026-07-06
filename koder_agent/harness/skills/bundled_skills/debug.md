---
name: debug
description: Debug an issue in the current Koder session by inspecting session state, enabling debug logging, and diagnosing errors from real output. Use when Koder itself misbehaves, a tool call fails unexpectedly, or the session acts strangely.
argument_hint: "[issue description]"
disable_model_invocation: true
---

Debug the current Koder session. Issue as described by the user: $ARGUMENTS
(if empty, ask what went wrong before touching anything).

## Scope fence

This skill debugs **Koder itself** — the session, its tools, configuration, providers, and
MCP servers. It is NOT for debugging the user's application code; for that, investigate the
app's own logs and tests directly. If the issue turns out to be in the user's project rather
than in Koder, say so and switch to normal debugging.

## The debug-logging boundary — read this first

Debug logging captures **nothing from before it was enabled**. Koder runs at FATAL log level
by default; verbose diagnostics exist only in sessions started with `koder --debug`.

- If this session was NOT started with `--debug`: you have no debug trace of the failure.
  Tell the user plainly, then ask them to restart with `koder --debug` (add `--resume` to
  keep this session's history), **reproduce the issue**, and come back. Do not pretend the
  missing trace exists or reconstruct it from memory.
- If it WAS started with `--debug`: diagnostics stream to the terminal. Ask the user to
  reproduce the issue now, then read what actually appeared — do not analyze stale output
  from before the reproduction.

## Where session state lives

| What | Where |
|---|---|
| Conversation history, session metadata, MCP server configs | SQLite DB at `~/.koder/koder.db` |
| Persistent memory files | `.koder/memory/` (project) and `~/.koder/memory/` (user) |
| Settings | `~/.koder/config.yaml`, `~/.koder/settings.json`, project `.koder/settings.json` |
| Skills | `.koder/skills/` (project) and `~/.koder/skills/` (user) |
| Project context | `AGENTS.md` in the working directory |

Inspect the DB read-only when needed, e.g. via run_shell:
`sqlite3 ~/.koder/koder.db ".tables"` then targeted `SELECT` queries. Never write to it.

## Phases

1. **Pin down the symptom.** Restate the issue in one sentence: what the user did, what they
   expected, what happened instead. If $ARGUMENTS is vague, ask one clarifying question now.
2. **Check the obvious environment causes.** With read_file and run_shell, look at the
   settings files above, relevant environment variables (`KODER_MODEL`, `KODER_API_KEY`,
   `KODER_BASE_URL`, provider keys), and `koder mcp list` output if MCP servers are involved.
   Config precedence is CLI args > environment variables > `~/.koder/config.yaml` > defaults.
3. **Get a real trace.** Apply the debug-logging boundary above: have the user reproduce the
   issue under `--debug`, then read the actual output. Fetch real evidence before proposing
   any fix — no assumption-based debugging.
4. **Summarize errors and warnings.** Quote the exact error lines, stack traces, or failure
   patterns you observed. Distinguish Koder errors (tool failures, provider/API errors, MCP
   connection errors) from application errors that merely surfaced through Koder.
5. **Suggest concrete next steps.** Each suggestion names the file or command involved: a
   specific setting to change, a key to set, a server to re-add, or — if it looks like a
   Koder bug — a minimal reproduction to report at https://github.com/feiskyer/koder.

## Anti-patterns

- Diagnosing from memory of "what usually breaks" instead of from captured output.
- Editing `~/.koder/koder.db` or deleting session data as a "fix."
- Claiming the issue is resolved without having the user reproduce the original failure path.
- Burying the finding: the first line of your report states the root cause or the single
  most likely cause, then the evidence.
