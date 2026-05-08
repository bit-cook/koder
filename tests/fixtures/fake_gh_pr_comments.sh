#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
printf '%s\n' "$*" >> "$HOME/.koder/pr-comments-gh.log"

state_file="$HOME/.koder/pr-comments-state"
state="$(cat "$state_file" 2>/dev/null || printf normal)"

if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  if [ "$state" = "no-pr" ]; then
    echo "no pull request" >&2
    exit 1
  fi
  printf '%s\n' '{"number":123,"headRepository":{"name":"demo","owner":{"login":"octo"}}}'
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "/repos/octo/demo/issues/123/comments" ]; then
  if [ "$state" = "api-fail" ]; then
    echo "issue comments unavailable" >&2
    exit 1
  fi
  if [ "$state" = "empty" ]; then
    printf '%s\n' '[]'
    exit 0
  fi
  printf '%s\n' '[{"id":1,"user":{"login":"alice"},"body":"Top-level PR comment"}]'
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "/repos/octo/demo/pulls/123/comments" ]; then
  if [ "$state" = "api-fail" ]; then
    echo "review comments unavailable" >&2
    exit 1
  fi
  if [ "$state" = "empty" ]; then
    printf '%s\n' '[]'
    exit 0
  fi
  printf '%s\n' '[{"id":10,"user":{"login":"bob"},"path":"src/app.py","line":42,"body":"Escape this value","diff_hunk":"@@ -1 +1 @@\n-old\n+new"},{"id":11,"user":{"login":"carol"},"in_reply_to_id":10,"body":"Agreed"}]'
  exit 0
fi

echo "unsupported gh args: $*" >&2
exit 1
