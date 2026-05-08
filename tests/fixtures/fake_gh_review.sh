#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
printf '%s\n' "$*" >> "$HOME/.koder/review-gh.log"

if [ "${1:-}" = "pr" ] && [ "${2:-}" = "diff" ]; then
  pr="${3:-}"
  case "$pr" in
    123)
      cat <<'DIFF'
diff --git a/pr_review.py b/pr_review.py
index 1111111..2222222 100644
--- a/pr_review.py
+++ b/pr_review.py
@@ -1,2 +1,3 @@
 def handle(request):
-    return "ok"
+    token = request.GET.get('token')
+    return token
DIFF
      exit 0
      ;;
    0)
      echo "gh pr diff failed for scenario" >&2
      exit 1
      ;;
  esac
fi

echo "unexpected gh invocation: $*" >&2
exit 1
