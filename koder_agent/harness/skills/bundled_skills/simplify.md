---
name: simplify
description: Review the changed code for reuse, simplification, efficiency, and altitude cleanups, then apply the fixes. Quality only - it does not hunt for bugs; use code-review for correctness. Use when asked to simplify, clean up, refactor for clarity, or polish recently changed code.
---

`/simplify → 4 cleanup angles → apply the fixes → lint and test`

## Scope fence

You are improving the **quality** of the changed code, not hunting for bugs.
Review it for reuse, simplification, efficiency, and altitude issues, then fix
what you find. Do NOT report or chase correctness bugs — that is what the
code-review skill is for. Every fix here must preserve behavior; if a cleanup
would change what the code does, it is out of scope, note it and move on.

## Phase 0 — Gather the diff

Build the review scope with git_command, falling back down this chain until a
non-empty diff appears:

1. `git diff @{upstream}...HEAD` — the branch's commits vs its upstream (merge-base form).
2. No upstream: `git diff origin/main...HEAD`, or `git diff main...HEAD` if `origin/main` does not exist.
3. Still empty or refs missing: `git diff HEAD~1`.

Then always run `git diff HEAD` and include uncommitted working-tree changes —
this often runs before the commit. If a branch, ref range, or path was passed
as an argument, review that instead. If the combined diff is empty, say so and
stop. The diff is the review scope: only code the diff touches is fair game.

## Phase 1 — Review from four angles

For a large diff (several files or 200+ changed lines), launch **4 parallel
reviewers in a single task_delegate call**, one per angle below; each prompt
must be self-contained: include the diff (or the exact git_command to
reproduce it), the angle's instructions verbatim, and the required output
shape — `file`, `line`, one-line summary, and the concrete cost (what is
duplicated, wasted, or harder to maintain). For a small diff, run the four
angles yourself as sequential passes; do not spawn agents to read fifty lines.

### Reuse

Flag new code that re-implements something the codebase already has. Use
grep_search over shared/utility modules and files adjacent to the change to
find existing helpers, and **name the existing helper to call instead** — a
reuse finding without a named replacement is not a finding.

### Simplification

Flag unnecessary complexity the diff adds: needless indirection, redundant or
derivable state, copy-paste with slight variation, deep nesting, dead
branches or dead code left behind, over-general abstractions with exactly one
caller. Name the simpler form that does the same job.

### Efficiency

Flag obvious wasted work the diff introduces **on hot paths only**: redundant
computation or repeated I/O in loops, independent operations run sequentially
that could batch, blocking work added to startup. Name the cheaper
alternative. Do not micro-optimize cold paths — that is churn, not cleanup.

### Altitude

Check each change sits at the right level of abstraction. Special cases
layered onto shared infrastructure signal the fix is not deep enough — prefer
generalizing the underlying mechanism. Inline single-use helpers that only
add a name to three lines; extract genuinely repeated logic that the diff
copies a second or third time.

## Phase 2 — Apply the fixes

Collect all findings, dedup ones pointing at the same line or mechanism, and
apply each remaining fix directly with edit_file, preserving behavior. Skip
any finding whose fix would change intended behavior, require edits well
outside the reviewed diff, or that you judge a false positive — record the
skip with a one-line reason rather than arguing with it.

## Phase 3 — Verify and summarize

Run the project's lint and tests on what you touched, using commands from
AGENTS.md, README, CI, or manifest scripts. Scope them to changed files and
relevant test files when the project tooling supports that. A cleanup that
breaks a test was not behavior-preserving — revert or repair it before
reporting.

Finish with a brief summary: for each applied cleanup, one line of
before/after rationale (what it was, what it is now, why that is better), then
the list of skipped findings with reasons. If the code was already clean, say
exactly that instead of inventing findings.
