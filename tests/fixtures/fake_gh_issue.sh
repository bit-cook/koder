#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
printf '%s\n' "$*" >> "$HOME/.koder/issue-gh.log"

state_file="$HOME/.koder/issue-state"
state="$(cat "$state_file" 2>/dev/null || printf list)"

if [ "${1:-}" = "issue" ] && [ "${2:-}" = "list" ]; then
  if [ "$state" = "fail" ]; then
    echo "gh issue list failed for scenario" >&2
    exit 2
  fi
  if [ "$state" = "empty" ]; then
    exit 0
  fi
  printf '%s\n' '42\tFix flaky tmux validation\tbug\tOPEN'
  printf '%s\n' '43\tDocument release checklist\tdocs\tOPEN'
  exit 0
fi

echo "unsupported gh args: $*" >&2
exit 1
