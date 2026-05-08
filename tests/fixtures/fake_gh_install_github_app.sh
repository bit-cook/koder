#!/bin/sh
set -eu

mkdir -p "$HOME/.koder"
log="$HOME/.koder/install-github-app-gh.log"
workflow_root="$HOME/.koder/remote-workflows"

log_line() {
  printf '%s\n' "$*" >> "$log"
}

decode_content() {
  content="$1"
  output="$2"
  mkdir -p "$(dirname "$output")"
  if printf '%s' "$content" | base64 --decode > "$output" 2>/dev/null; then
    return 0
  fi
  if printf '%s' "$content" | base64 -D > "$output" 2>/dev/null; then
    return 0
  fi
  printf '%s' "$content" > "$output.b64"
}

if [ "${1:-}" = "--version" ]; then
  log_line "version"
  printf '%s\n' "gh version 2.99.0 (scenario)"
  exit 0
fi

if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
  log_line "auth status -h github.com"
  printf '%s\n' "Token scopes: repo, workflow"
  exit 0
fi

if [ "${1:-}" = "secret" ] && [ "${2:-}" = "set" ]; then
  name="${3:-}"
  repo=""
  shift 3
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--repo" ]; then
      repo="${2:-}"
      shift 2
      continue
    fi
    shift
  done
  secret_value="$(cat)"
  bytes="$(printf '%s' "$secret_value" | wc -c | tr -d ' ')"
  matches_env="no"
  if [ "$secret_value" = "${KODER_API_KEY:-}" ]; then
    matches_env="yes"
  fi
  log_line "secret set $name --repo $repo stdin-bytes=$bytes stdin-matches-env=$matches_env"
  printf '%s\n' "{}"
  exit 0
fi

if [ "${1:-}" = "variable" ] && [ "${2:-}" = "set" ]; then
  name="${3:-}"
  body=""
  repo=""
  shift 3
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --body)
        body="${2:-}"
        shift 2
        ;;
      --repo)
        repo="${2:-}"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done
  log_line "variable set $name --body $body --repo $repo"
  printf '%s\n' "{}"
  exit 0
fi

if [ "${1:-}" = "api" ]; then
  shift
  method="GET"
  if [ "${1:-}" = "--method" ]; then
    method="${2:-}"
    shift 2
  fi
  endpoint="${1:-}"
  shift || true

  jq_expr=""
  ref=""
  sha=""
  branch=""
  content=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --jq)
        jq_expr="${2:-}"
        shift 2
        ;;
      -f)
        field="${2:-}"
        case "$field" in
          ref=*) ref="${field#ref=}" ;;
          sha=*) sha="${field#sha=}" ;;
          branch=*) branch="${field#branch=}" ;;
          content=*) content="${field#content=}" ;;
        esac
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done

  if [ "$method" = "GET" ] && [ "$endpoint" = "repos/acme/demo" ]; then
    log_line "api GET $endpoint --jq $jq_expr"
    case "$jq_expr" in
      .permissions.admin)
        printf '%s\n' "true"
        exit 0
        ;;
      .default_branch)
        printf '%s\n' "main"
        exit 0
        ;;
    esac
  fi

  if [ "$method" = "GET" ] && [ "$endpoint" = "repos/acme/demo/git/ref/heads/main" ]; then
    log_line "api GET $endpoint --jq $jq_expr"
    printf '%s\n' "abc123"
    exit 0
  fi

  case "$method $endpoint" in
    "POST repos/acme/demo/git/refs")
      log_line "api POST $endpoint ref=$ref sha=$sha"
      printf '%s\n' "{}"
      exit 0
      ;;
  esac

  case "$endpoint" in
    repos/acme/demo/contents/*\?ref=setup/koder)
      log_line "api GET $endpoint --jq $jq_expr"
      printf '%s\n' "Not Found" >&2
      exit 1
      ;;
    repos/acme/demo/contents/*)
      if [ "$method" = "PUT" ]; then
        path="${endpoint#repos/acme/demo/contents/}"
        output="$workflow_root/$path"
        decode_content "$content" "$output"
        log_line "api PUT $endpoint branch=$branch content_file=$output"
        printf '%s\n' "{}"
        exit 0
      fi
      ;;
  esac
fi

log_line "unsupported $*"
printf '%s\n' "unsupported gh args: $*" >&2
exit 1
