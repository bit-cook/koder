#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
printf '%s\n' "$*" >> "$HOME/.koder/autofix-pr-gh.log"

state_file="$HOME/.koder/autofix-pr-state"
state="$(cat "$state_file" 2>/dev/null || printf diff)"

if [ "${1:-}" = "pr" ] && [ "${2:-}" = "diff" ]; then
  pr_number="${3:-}"
  if [ "$state" = "fail" ]; then
    echo "gh pr diff failed for scenario" >&2
    exit 2
  fi
  if [ "$state" = "empty" ]; then
    exit 0
  fi
  printf '%s\n' "diff --git a/src/app.py b/src/app.py"
  printf '%s\n' "@@ -1,2 +1,2 @@"
  printf '%s\n' "-old line for PR $pr_number"
  printf '%s\n' "+new line for PR $pr_number"
  printf '%s\n' " context line"
  exit 0
fi

echo "unsupported gh args: $*" >&2
exit 1
