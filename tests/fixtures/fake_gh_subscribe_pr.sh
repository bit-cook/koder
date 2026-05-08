#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
printf '%s\n' "$*" >> "$HOME/.koder/subscribe-pr-gh.log"

state_file="$HOME/.koder/subscribe-pr-state"
state="$(cat "$state_file" 2>/dev/null || printf list)"

if [ "${1:-}" = "pr" ] && [ "${2:-}" = "list" ]; then
  if [ "$state" = "fail" ]; then
    echo "gh pr list failed for scenario" >&2
    exit 2
  fi
  if [ "$state" = "empty" ]; then
    exit 0
  fi
  printf '%s\n' '17\tImprove tmux validation UX\tfeature\tOPEN'
  printf '%s\n' '18\tFix docs counter drift\tdocs\tOPEN'
  exit 0
fi

echo "unsupported gh args: $*" >&2
exit 1
