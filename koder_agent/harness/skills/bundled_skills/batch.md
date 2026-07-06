---
name: batch
description: Orchestrate a large parallelizable change across the codebase by decomposing it into 5-30 independent units and running one worker agent per unit, each with a self-contained brief and verification recipe.
argument_hint: "<instruction>"
disable_model_invocation: true
---

# Batch: Parallel Work Orchestration

You are orchestrating a large, parallelizable change across this codebase.

## User instruction

$ARGUMENTS

## Phase 1 — Research and decompose

1. **Understand the scope.** Delegate research via task_delegate (or search
   directly with glob_search/grep_search if the scope is small): find every
   file, pattern, and call site the instruction touches, and the existing
   conventions the change must follow so the result is consistent. Do not
   spawn a single worker until you can enumerate the affected surface.

2. **Decompose into independent units.** Break the work into **5–30**
   self-contained units. Each unit must be:
   - **Independently implementable** — a worker in an isolated git worktree
     could complete it with no shared state or ordering dependency on sibling
     units;
   - **Mergeable on its own** — the codebase builds and tests pass with only
     this unit's change applied;
   - **Roughly uniform in size** — split oversized units, merge trivial ones.

   Scale the count to the work: a handful of files → closer to 5; hundreds →
   closer to 30. Prefer per-directory or per-module slicing over arbitrary
   file lists. If the units cannot be made independent (every change funnels
   through one shared file), batch is the wrong tool — say so and do the work
   sequentially instead.

3. **Determine the verification recipe FIRST.** Before spawning anything,
   decide how a worker proves its unit works end-to-end — not just that unit
   tests pass. Look for: an existing test suite scoped to the touched area, a
   CLI invocation that exercises the changed behavior, a dev-server + curl
   pattern for API changes, or a build/lint gate that catches the failure
   class this change risks. Write the recipe as short concrete steps
   (setup command, exact verification command, expected outcome).

   **If you cannot find a concrete verification path, STOP and ask the user
   before spawning anything**, offering 2–3 specific options based on what
   you found (e.g. "run `uv run pytest tests/x/` per unit", "run the CLI
   against a sample input and compare output", "no e2e — compile/lint check
   only"). Do not skip this: workers cannot ask the user themselves, and 20
   workers guessing at verification is 20 unverified changes.

4. **Record the plan** with todo_write: one todo per unit, plus the shared
   verification recipe and worker template. Present the unit list to the user
   before executing if the decomposition involved judgment calls.

## Phase 2 — Run the workers

Spawn one worker per unit via task_delegate; put independent units in a
single task_delegate call (a list of tasks) so they run in parallel. Group
into waves of 5–10 if the unit count is large. Each worker prompt must be
**fully self-contained** — the worker sees none of your context. Include:

- The overall goal (the user's instruction, verbatim);
- This unit's task: title, exact file paths, and the precise change to make;
- The codebase conventions you discovered that the worker must follow;
- The verification recipe, copied verbatim, with the instruction to actually
  run it and report the observed output;
- Required report shape, ending with a single status line:
  `UNIT: <title> — DONE | FAILED — <one-line evidence or reason>`.

Tell workers NOT to commit — you integrate at the end. Tell them not to touch
files outside their unit's list; overlapping edits are how parallel work
corrupts itself.

## Phase 3 — Track and integrate

Track each unit's status with todo_write as results return, and keep a status
table for the user:

| # | Unit | Status | Evidence |
|---|------|--------|----------|

Parse each worker's `UNIT:` line; treat a missing or malformed status line as
FAILED. For failed units, read the worker's output, decide whether to retry
with a corrected brief (once) or fix it yourself; do not silently drop a unit.

When all units report, run the **final integration check** yourself: the full
verification recipe across the combined result, plus the project's standard
gates discovered from AGENTS.md, README, CI, or manifest scripts. Workers
verify units in isolation; only this step proves the units compose. Finish
with a one-line summary ("14/15 units landed; unit 7 skipped — reason") and
the list of changed files. Leave changes uncommitted unless the user asked
otherwise.
