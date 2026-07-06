---
name: review-spec
description: Review changes since a fixed point along two axes - Standards (does the code follow this repo's documented conventions?) and Spec (does the code match what the originating issue/PRD/plan asked for?) - running both as parallel subagents and reporting side by side. Use when asked to review a branch against its spec, check conformance to repo standards, or review work against requirements.
argument_hint: "[base-ref or PR]"
---

Two-axis review of the changes since a fixed point: `$ARGUMENTS`.

- **Standards** — does the code conform to this repo's documented conventions?
- **Spec** — does the code faithfully implement what the originating
  issue / PRD / plan asked for?

Both axes run as **parallel subagents** so they cannot pollute each other's
context; this skill then reports their findings side by side. A change can
pass one axis and fail the other — code that follows every convention but
implements the wrong thing, or code that does exactly what the issue asked
while breaking the project's conventions. Reporting them separately stops one
axis from masking the other.

## Phase 0 — Pin the fixed point and gather the diff

If `$ARGUMENTS` names a ref, range, or PR (a branch name, commit SHA, tag,
`HEAD~3`, `#123`, a PR URL), use it **verbatim** as the fixed point — do not
second-guess it. For a PR, resolve its base branch (e.g.
`gh pr view <n> --json baseRefName` via `run_shell`) and use that as the ref.

If no fixed point was given, build the review scope with `git_command`,
falling back down this chain until a non-empty diff appears:

1. `git diff @{upstream}...HEAD` — the branch's own commits vs its upstream
   (merge-base form).
2. If there is no upstream: `git diff origin/main...HEAD`, or
   `git diff main...HEAD` if `origin/main` does not exist.
3. If that is empty or the refs are missing: `git diff HEAD~1`.

Then always run `git diff HEAD` and include any uncommitted working-tree
changes in scope — reviews often run before the commit. Capture the diff
**once**, using the three-dot form (`git diff <fixed-point>...HEAD`) so the
comparison is against the merge-base, and note the commit list via
`git log <fixed-point>..HEAD --oneline`. If the combined diff is still empty,
say so and stop; do not invent a review. Treat the resulting unified diff as
the single source of truth for what changed.

## Phase 1 — Identify the spec source

Search for the originating spec in this order, stopping at the first hit:

1. **Issue/ticket references in the branch's commits.** Read
   `git log <fixed-point>..HEAD` (full messages, not `--oneline`) and look for
   `#123`, `Closes/Fixes #N`, issue-tracker URLs, or ticket IDs. Fetch the
   referenced issue with `web_fetch`, or `gh issue view <n>` / `gh pr view <n>`
   via `run_shell` when it lives on GitHub.
2. **A file the user passed.** If `$ARGUMENTS` includes a path to a spec,
   plan, or PRD file, `read_file` it.
3. **Spec/plan files in the repo.** Use `grep_search` over `docs/`, `specs/`,
   `plans/`, `tasks/`, and the repo root for files mentioning this feature or
   the branch name.
4. **Nothing found — ask.** Ask the user where the spec is before proceeding.
   A spec review without a spec is fiction; do not reconstruct requirements
   from the diff itself. If the user says there is none, run the Standards
   axis alone and state in the report that the Spec axis was skipped.

## Phase 2 — Collect the standards sources

List everything in the repo that documents how code should be written:

- `AGENTS.md` at the repo root, plus any `AGENTS.md` in an ancestor directory
  of a changed file (use `glob_search`).
- `CONTRIBUTING.md`, `STYLE.md`, `STANDARDS.md`, and style guides under
  `docs/`.
- Lint and formatter configs (`.editorconfig`, `ruff`/`eslint`/`prettier`
  configs, `pyproject.toml` tool sections) — these encode enforced standards;
  note them, but do not re-check what the tooling already enforces.
- Conventions evident in neighboring code of the changed files (dominant
  patterns count as standards only when they are near-universal).

## Phase 3 — Launch both reviewers in parallel

Dispatch **two subagents in parallel** via a single `task_delegate` call. Each
prompt must be self-contained: include the diff (or a faithful summary plus
the full text of the relevant hunks), the changed-file list, the commit list,
and the exact source files that reviewer should read.

- **Standards reviewer.** Brief: read the standards sources listed for you,
  then walk the diff. For each violation, cite the standard's source (file +
  section or rule) AND the offending `file:line` in the diff. **No un-cited
  findings** — if you cannot point at a documented rule or a dominant
  codebase convention, it is not a standards violation; do not report taste.
  Distinguish hard violations from judgment calls, and skip anything the
  lint/format tooling already enforces.
- **Spec reviewer.** Brief: read the spec, extract its concrete requirements
  as a checklist, then walk the diff against it. Mark each requirement
  **met / partially met / not met / cannot determine** — with `file:line`
  evidence for met items, and a statement of exactly what is missing for the
  others. Separately flag implemented behavior the spec did NOT ask for
  (scope creep), quoting the diff hunk; do not mix scope creep into the
  requirements checklist.

## Phase 4 — Report side by side

Present two sections, keeping the axes separate — do not merge or rerank
findings across them:

- `## Standards` — each finding as `file:line` + the violated rule and its
  source, most severe first.
- `## Spec` — the requirements checklist with statuses and evidence, followed
  by a `Scope creep` subsection.

End with a short **combined verdict**: total findings per axis, whether the
change is mergeable on each axis, and the single worst issue if any. Every
finding must be actionable with a `file:line` reference. **No findings on an
axis is a valid, stated outcome** — write "no standards violations found" or
"all spec requirements met" rather than padding the report to appear thorough.
