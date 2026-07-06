---
name: code-review
description: Review the current diff for correctness bugs at a chosen effort level (low, medium, or high). Use when the user asks to review changes, a branch, a PR, or the working tree for bugs before committing or merging.
argument_hint: "[low|medium|high]"
---

Review the current changes for bugs. Effort level: `$ARGUMENTS` — one of `low`,
`medium`, or `high`. If no level was given (or the argument is not one of
those), run at **medium**. Any extra argument (a branch, ref range, PR number,
or path) is the review target and overrides the default diff below.

## Scope fence

This is a **correctness review, not a style review**. Report bugs: code that
produces wrong output, crashes, loses data, or breaks a caller. Do NOT report
naming preferences, formatting, missing tests, or subjective structure opinions.
The only style findings allowed are violations of a rule the repository itself
documents (AGENTS.md or an equivalent written standard) — and then you must
quote the rule and its source file. Skip test/fixture-only hunks (`tests/`,
`test/`, `__tests__/`, `*_test.*`, `*.test.*`, `fixtures/`, `testdata/`) at
low effort; at medium and high, review them only for bugs that weaken real
coverage (deleted assertions, tautological checks).

## Phase 0 — Gather the diff (all levels)

Build the review scope with `git_command`, falling back down this chain until a
non-empty diff appears:

1. `git diff @{upstream}...HEAD` — the branch's own commits vs its upstream
   (merge-base form).
2. If there is no upstream: `git diff origin/main...HEAD`, or
   `git diff main...HEAD` if `origin/main` does not exist.
3. If that is empty or the refs are missing: `git diff HEAD~1`.

Then always run `git diff HEAD` and include any uncommitted working-tree
changes in scope — reviews often run before the commit. If the combined diff is
still empty, say so and stop; do not invent a review. Treat the resulting
unified diff as the single source of truth for what changed.

---

## Level: low — one pass, precision

`1 self-pass over the diff → no subagents → no verification → ≤4 findings`

Stance: **precision**. Report only findings you would stake your reputation
on — a maintainer reading each one should immediately agree it is a bug.

1. Read the unified diff yourself, hunk by hunk. No `task_delegate`, no
   full-file reads beyond what a hunk's context requires.
2. Flag only runtime-correctness bugs visible from the hunk alone:
   inverted/wrong condition, off-by-one, null/None deref where nearby lines
   show the value can be absent, removed guard, falsy-zero check, missing
   `await`, wrong-variable copy-paste, error swallowed in a catch that should
   propagate, dead code the diff leaves behind.
3. Output at most **4 findings**, most severe first, using the Output format
   below. If nothing qualifies, output exactly `(none)`.

## Level: medium — finders + verification, balanced

`3-4 finder subagents → dedup → verify each yourself → ≤8 findings`

Stance: **balanced**. Finders lean toward recall; your verification pass
restores precision. Every reported finding should be one a maintainer would
act on.

### Phase 1 — Finders

Dispatch **3-4 finder subagents in parallel** via a single `task_delegate`
call (one task per angle). Give each finder the full diff text, the repo root,
and its angle brief below. Each finder returns up to 6 candidates, each with
`file`, `line`, a one-line `summary`, and a concrete `failure_scenario`.

- **Angle A — line-by-line diff scan.** Read every hunk line by line, then
  `read_file` the enclosing function for each hunk — bugs on unchanged lines
  of a touched function are in scope. For every line ask: what input, state,
  timing, or platform makes this line wrong?
- **Angle B — removed-behavior audit.** For every line the diff DELETES or
  replaces, name the invariant or behavior it enforced, then search the new
  code (`grep_search`) for where that behavior is re-established. If you
  cannot find it, that is a candidate: a removed guard, a dropped error path,
  a narrowed validation, a deleted test covering a real case.
- **Angle C — edge and error paths.** Walk each changed function through its
  unhappy paths: empty/None/zero inputs, boundary indices, error returns and
  exceptions, timeouts, partial failures, cleanup on early exit. Flag paths
  the change breaks or forgets.
- **Angle D — conventions vs repo standards.** Locate the standards that
  govern the changed files: the repo-root AGENTS.md plus any AGENTS.md in an
  ancestor directory of a changed file (use `glob_search`). Flag a violation
  only when you can quote the exact rule, cite its source file, and quote the
  diff line that breaks it. No "spirit of the doc" inferences; if no standard
  applies, return nothing.

### Phase 2 — Verify yourself

Dedup candidates that point at the same line/mechanism, keeping the one with
the most concrete failure scenario. Then verify **each remaining candidate
yourself** — `read_file` the actual code, do not trust the finder's summary.
Keep a candidate only if you can state the failing input/state and point at
`file:line` evidence in the current code; drop it if the code does not say
what the finder claims or a guard elsewhere handles it (note the guard's
`file:line` to yourself before dropping).

Report at most **8 findings**, most severe first. Correctness bugs outrank
conventions findings when the cap forces a cut.

## Level: high — wide net, recall

`6+ finder angles → dedup → verify with CONFIRMED/PLAUSIBLE verdicts → ≤10 findings`

Stance: **recall**. Catch every real bug a careful reviewer would catch in one
sitting — at this level, catching real bugs matters more than avoiding false
positives. Err on the side of surfacing.

### Phase 1 — Finders

Dispatch **at least 6 finder subagents in parallel** via `task_delegate`:
Angles A-D from the medium level, plus:

- **Angle E — security.** Injection (SQL/shell/path/template), missing or
  weakened authn/authz checks, secrets or tokens written to logs or disk,
  unsafe deserialization, SSRF, path traversal, crypto misuse. Only flag
  issues this diff introduces or re-exposes.
- **Angle F — concurrency and state.** Shared mutable state without
  synchronization, check-then-act races, missing/shrunk lock scope, async
  tasks whose results or exceptions are never awaited, caches or singletons
  mutated per-request, ordering assumptions between callbacks, reentrancy.

If two angles flag the same line for different reasons, record both — do not
let one angle's conclusion suppress another's.

### Phase 2 — Verify with verdicts

Dedup, then verify each candidate yourself against the code and classify:

- **CONFIRMED** — you can name the triggering inputs/state and the wrong
  output or crash, AND you quoted the evidence both ways: the line(s) that
  prove the bug and the place a disproving guard would live (showing it is
  absent).
- **PLAUSIBLE** — the mechanism is real but the trigger is uncertain (timing,
  environment, config). Default to PLAUSIBLE rather than dropping: races,
  None on a rare-but-reachable path, falsy-zero treated as missing, boundary
  off-by-one the code does not exclude, retry/partial-failure behavior. State
  what would confirm it.
- Drop only when refutation is constructible from the code: the code does not
  say that (quote the actual line), it is provably impossible
  (type/constant/invariant), or a guard in this same diff handles it (cite
  the guard's `file:line`).

Report at most **10 findings**, ranked most severe first, each labeled with
its **verdict** (`CONFIRMED` or `PLAUSIBLE`).

---

## Finder instructions (embed in every finder prompt)

Finders must not self-censor: report anything that might be a real bug —
deduplication and judgment happen downstream in the verify phase, not inside
the finder. Do not pre-filter candidates for politeness, do not drop a
candidate because you are only 60% sure, and do not soften a summary to hedge.
Finders that silently drop half-believed candidates bypass verification and
are the dominant cause of missed bugs. Every candidate must still name a
concrete failure scenario — "this looks suspicious" with no failing input is
not a candidate.

## Verifier rules

A finding survives only with a complete failure story: *these inputs or this
state* → *this wrong behavior*. Verification means reading the current code,
not the candidate text. Quote the lines that prove the defect, and check (and
be ready to quote) the lines that would disprove it — callers, guards, and
defaults elsewhere in the file or its call sites (`grep_search` the symbol).
"The code looks wrong" is not evidence; a quoted line plus a named trigger is.

## Output format

Present the surviving findings as a markdown list, most severe first. One
entry per finding:

- **[severity: critical|major|minor] `path/to/file.ext:123`** — one-sentence
  statement of the defect.
  - Failure scenario: concrete inputs/state → wrong output/crash.
  - Suggested fix: one concrete change (a line edit, a guard to add, a helper
    to call).
  - Verdict: CONFIRMED or PLAUSIBLE (high effort only).

If nothing survives, output exactly `(none)` — an empty review is a valid
result; do not pad with style commentary to appear thorough.
