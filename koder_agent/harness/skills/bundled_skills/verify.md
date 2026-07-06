---
name: verify
description: Verify that a code change actually does what it's supposed to by running the app and observing real behavior. Use when asked to verify a change, confirm a fix works, test a feature manually, or validate local changes before pushing.
argument_hint: "[what to verify]"
---

Verify the change described by: $ARGUMENTS (if empty, verify the pending changes in this repository).

**Verification is runtime observation.** You run the application, drive it to the point where
the changed code executes, and capture what you actually see. That captured output is your
evidence. Nothing else is.

**Do not run tests or typecheckers as "verification."** Running them proves you can run CI,
not that the change works. Not as a warm-up, not "just to be sure," not as a regression sweep
afterwards. Spend that time running the app instead.

**Do not import-and-call internal functions.** Importing a function from source and printing
its return value is a unit test you just wrote: the function did what the function does, which
you already knew from reading it. The app never ran. Whatever calls that code in the real
codebase ends at a CLI, a server endpoint, or a UI. Go there.

## Step 1 — Identify the change

The diff is ground truth. Any description of the change (a commit message, a PR body,
$ARGUMENTS itself) is a claim about the diff, and claims get checked. Read both; a mismatch
between them is a finding.

Establish the full range with git_command — a branch may hold several commits, and the change
may still be uncommitted:

- `git log --oneline @{u}..` then `git diff @{u}.. --stat` — if an upstream is set
- `git diff main...HEAD --stat` — no upstream, committed against the default branch
- `git diff HEAD --stat` — uncommitted working-tree changes vs HEAD

State how many commits are in scope. If no diff shows up from any of these and there is no
named target, say so and stop. Read the full diff, not just the stat.

## Step 2 — Find the real entry point

The surface is where a user — human or programmatic — meets the change. That is where you
observe. An internal function is never a surface: something calls it, and that caller ends at
one of these.

| Change reaches | Surface | You |
|---|---|---|
| CLI / TUI | terminal | run the command via run_shell, capture stdout/stderr |
| Server / API | socket | start the server, send a real request, capture the response |
| Library | package boundary | a sample script importing the installed package's public export, not `./src/...` internals |
| Config / prompt | the consuming program | run the program that consumes it, observe changed behavior |

Locate the entry point from the repo itself: README, pyproject.toml / package.json /
Makefile scripts, existing run instructions in AGENTS.md. Use glob_search and grep_search to
trace from the changed code outward to whatever invokes it.

**Tests in the diff are the author's evidence, not a surface.** A tests-only diff means there
is nothing to run here — report SKIP in one line. For mixed src+tests diffs, verify the src
and ignore the test files. Reading a test to learn the expected behavior is fine — it is a
spec — but then go run the app.

## Step 3 — Run it and drive the path

Run the app with run_shell. For servers, watchers, or anything long-running, set
`run_in_background=true`, poll readiness with shell_output, and clean up with shell_kill when
done. Isolate shared state where possible: unused ports, temp dirs from `mktemp -d`.

Take the smallest path that makes the changed code execute:

- Changed a flag? Run the command with it.
- Changed a handler? Start the server and hit that route with curl.
- Changed error handling? Trigger the error on purpose.
- Changed an internal function? Find the command or request that reaches it, and run that.

Read your plan back before executing it. If every step is build / typecheck / run-test-file,
you have planned a CI rerun, not a verification — find a step that reaches the surface or
report that you could not.

Once the happy path checks out, probe around it at the same surface: an empty or malformed
value for a new flag, a wrong method or missing field for a new route, the adjacent error the
refactor did not touch. One or two probes chosen by what the diff points at — not a checklist.

**Destructive paths:** if the change deletes, publishes, sends, or writes outside the
workspace and there is no dry-run or safe target, do not drive it live. Verify around it and
state exactly which path you did not exercise and why.

## Step 4 — Capture evidence

Evidence is the app's own output: stdout, response bodies, exit codes, log lines, pane dumps.
Your memory or reasoning is not evidence. If output is unexpected, do not route around it —
capture it, then decide whether it is the change or the environment. Unrelated breakage you
tripped over is a finding, not noise.

## Step 5 — Report

Report inline in your final message:

- **Verdict: VERIFIED | NOT VERIFIED | BLOCKED | SKIP** — one line first.
- **Claim:** what the change is supposed to do, from your read of the diff; note any mismatch
  with the stated description.
- **Steps:** each step is one thing you did to the running app and the output you observed,
  with the evidence quoted or referenced. Build/install are setup, not steps. Test runs and
  typechecks do not belong here.
- **Findings:** anything that made you pause while running it — friction, odd defaults,
  unhelpful errors, pre-existing breakage — even when the verdict is VERIFIED.

Verdict meanings: VERIFIED means you ran the app and observed the change working at its
surface — not "tests pass" or "code looks right." NOT VERIFIED means you ran it and it does
not do what is claimed. BLOCKED means you could not reach a state where the change is
observable (broken build, missing dependency); say exactly where it stopped. SKIP means no
runtime surface exists (docs-only, types-only, tests-only); one line why.

**If runtime verification is impossible in this environment, say exactly that** — name the
missing piece (no network, no credentials, no display, dependency unavailable) and report
BLOCKED. Never substitute a test run for it and claim success. No partial pass: "3 of 4
worked" is NOT VERIFIED until the fourth works or is explained. When output is ambiguous,
report NOT VERIFIED with the raw capture attached rather than interpreting in the change's
favor — a false VERIFIED ships broken code; a false NOT VERIFIED costs one more look.
