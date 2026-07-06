---
name: stuck
description: Recover from being stuck by diagnosing why the current approach failed, then switching to a genuinely different one. Use after repeated failures on the same task, when retrying the same fix, or when progress has stalled.
---

You appear to be stuck. Stop pushing on the current approach and run this recovery
procedure. Do not skip step 1 — switching approaches without a diagnosis usually
reproduces the same failure in a new costume.

## Step 1 — Diagnose why the current approach failed

A failing approach almost always encodes a wrong assumption. Name it explicitly:

- What exactly were you trying to do, and what was the precise failure (quote the real
  error output — rerun the command via run_shell if you no longer have it)?
- What assumption does the approach depend on? (The API works this way; the file is
  where I think it is; the test failure means what I think it means; this library
  version supports that flag.)
- Which of those assumptions have you actually verified, versus inherited from your own
  earlier reasoning? Check the unverified one first — read_file the real code,
  grep_search for the real usage, run the real command.

Write the diagnosis in one or two sentences: "The approach failed because I assumed X,
and X is false/unverified because Y." If you cannot name the broken assumption yet, the
next step is to gather the evidence that would reveal it — not to try again.

## Step 2 — List 3 genuinely different alternatives

Genuinely different means each rests on a **different assumption**, not the same plan
with a tweaked flag or one more log statement. Test: if the diagnosed wrong assumption
would sink an alternative too, it is a variation, not an alternative. Useful axes:

- Different layer: fix the caller instead of the callee; change config instead of code.
- Different information source: read the library source or docs (web_fetch) instead of
  inferring behavior; add instrumentation instead of reasoning about state.
- Different decomposition: reduce to a minimal reproduction; do the change manually once
  to learn the shape before automating it.
- Fresh eyes: delegate a focused investigation to a subagent via task_delegate with a
  self-contained prompt, so it is not anchored on your failed attempts.

## Step 3 — Pick one, with rationale

Choose the alternative that best attacks the broken assumption from step 1. State in one
sentence why this one and not the other two.

## Step 4 — Execute it immediately

Do it now, in this turn. Do not end your message with a plan, a list of options, or
"I will try X next" — that is the stalling pattern this skill exists to break.

## Step 5 — Escalate only after the alternative also fails

If the chosen alternative genuinely fails too, stop and escalate to the user with a
crisp blocker report: (a) the goal, (b) each approach tried and its exact failure
output, (c) the assumption you now believe is broken, (d) the specific decision or
information you need from them. "It doesn't work" is not a blocker report.

## Red-flag rationalizations

If you catch yourself thinking any of these, you are stuck and rationalizing, not working:

| Rationalization | Reality |
|---|---|
| "One more retry will work" (after 2 identical failures) | Same inputs, same code, same failure. Retrying is only valid for transient errors — and you would have evidence of transience. |
| "It's probably a caching / timing / environment flake" | Flakiness is a claim that needs evidence (it passed once, unmodified). Otherwise it is your bug. |
| "Let me add one more print/log and run it again" (3rd time) | Instrumentation rounds without a hypothesis are stalling. State what the print would prove first. |
| "The fix is close, just one more small edit" | Three consecutive "small edits" to the same spot means the mental model is wrong, not the spelling. |
| "I'll rewrite this part from scratch" | A rewrite without a diagnosis re-encodes the same wrong assumption with new syntax. |
| "The test/tool must be wrong" | Possible — but verify it against a known-good case before dismissing it. |
| "I'll just work around it and come back later" | The workaround hides the broken assumption and it will resurface. Diagnose now or escalate now. |
