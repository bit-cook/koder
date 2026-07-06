---
name: run
description: Launch and drive this project's app to see it working - find the real entry point, start it with run_shell, and interact with it (CLI invocation, HTTP requests, or UI). Use when asked to run the app, start the server, demo a change working, or check the app launches.
argument_hint: "[what to run or check]"
---

Run this project's app. Target: $ARGUMENTS (if empty, run the primary app of
this repository).

**Running means launching the actual app and interacting with it** — not the
test suite, not an import of an internal function with a print statement. Meet
the app where a user would: the CLI at its command line, the server at its
socket, the UI at its page. If the goal is to confirm a specific change works,
this skill is the how-to-launch layer — hold your observations to the verify
skill's evidence rules (runtime output only, no "tests pass" as proof).

## Step 1 — Does a project skill already cover this?

A project-specific run/dev skill is the repo's verified path: its author
already hit the obstacles and committed what worked. Check before improvising:

1. `glob_search` for `SKILL.md` under `.koder/skills/` and `~/.koder/skills/`.
2. `grep_search` their `description:` lines for launching, running, serving,
   or driving this app.
3. If one matches, `read_file` it and **follow it verbatim** — the exact
   commands, env vars, and workarounds it records. Do not paraphrase or skip
   steps that look optional.

If several plausibly match and none is clearly right, ask the user which unit
to run. If nothing covers running, fall back to the playbooks below.

## Step 2 — Detect the project type from real files

Read the manifests, not your assumptions: `package.json` (`scripts`, `bin`),
`pyproject.toml` (`[project.scripts]`, entry points), `Makefile` targets,
`docker-compose.yml`, `Procfile`, `Cargo.toml`, `go.mod`. The README is a
hypothesis — if it says `npm run dev`, confirm `dev` exists in
`package.json` scripts before trusting it. Then pick the matching playbook.

### CLI tool

- Find the real entry point: `[project.scripts]` in pyproject.toml,
  `bin` in package.json, or the built binary path.
- Run it through its runner so you exercise the installed surface:
  `uv run <cli>`, `npx <cli>`, `./target/release/<cli>` — not
  `python -m` a random module.
- Drive it with a small realistic invocation that produces output, not
  `--help` alone. `--help` proves the entry point resolves; a real
  subcommand with real input proves the app works.
- Capture stdout, stderr, and the exit code. A meaningful exit code
  (linter returning 1 on findings) is part of the observation.

### Server / API

- Start it in the background: `run_shell` with `run_in_background=true`.
  A foreground server blocks the shell and helps nobody.
- Poll `shell_output` until the listening line appears ("Listening on",
  "Uvicorn running", the port number) instead of sleeping a fixed time.
- Exercise a real endpoint with `curl`: a health route first, then the
  route relevant to the task. Read the response body, not just the code.
- When done, terminate the process with `shell_kill`. Prefer an unused
  port (`PORT=...`) if the default might collide.

### TUI / interactive terminal app

- Direct stdin interactivity is not supported by run_shell — the app takes
  over the terminal and the session hangs.
- For a smoke run, wrap it in a pty (with a piped quit keystroke or a
  timeout) to see startup output and confirm the app boots. The `script`
  syntax differs by OS: macOS/BSD `script -q /dev/null <cmd>`; Linux
  (util-linux) `script -qc "<cmd>" /dev/null`.
- If the task needs real interaction (navigate menus, toggle options) and
  no pty-driving tooling exists here, state that limitation explicitly and
  report what the smoke run did show. Do not fake interaction or claim the
  UI works from a boot log.

### Web frontend

- Start the dev server in the background (`run_shell` with
  `run_in_background=true`), poll `shell_output` for the ready/compiled line.
- `curl` the page URL to confirm it serves: check the HTTP status and that
  the body contains expected markup (a title, a root element, mounted
  content) — the first request may be slow while routes compile on demand.
- Visual verification needs a browser. If no browser tooling is available
  in this environment, say so explicitly: "the page serves and returns
  expected HTML; visual rendering was not verified." Never claim it "looks
  right" from an HTML string.
- Kill the dev server with `shell_kill` when done.

### Library / SDK

- There is no app to run. Build the smallest real consumer instead: a
  scratch script (in a temp dir) that imports the **installed package the
  way a user would** — the public package name, not `./src/...` internals —
  and calls its primary public API with realistic input.
- Run that script and capture its output. Label the result as a consumer
  demo of the public surface, not app verification — there is no app.

If nothing fits, start from the closest playbook and adapt. Docker-compose
or Procfile projects usually reduce to the server playbook with
`docker compose up -d` / the Procfile command as the launch line.

## Rules

- **Obstacles are content.** Every error you hit — missing dependency, port
  in use, unset env var, wrong node version — goes in the final report
  along with the fix that worked. That trail is often more valuable than
  the happy path.
- **Docs are hypotheses.** Commands from README or comments get checked
  against the manifest before you run them; when they diverge, the manifest
  wins and the divergence is a finding.
- **Drive it, don't just launch it.** A process that starts and idles proves
  the entry point resolves. Interact until you observe output a user would
  see, and quote that output.
- **Always clean up.** Kill every background process you started with
  `shell_kill` before finishing; leave no orphaned servers or watchers.
- **Report precisely:** what you launched (exact command), what you drove
  (invocations, requests, inputs), and what you observed (quoted output,
  status codes, exit codes) — plus every obstacle and its fix.
