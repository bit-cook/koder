"""Local GitHub Actions setup helpers for Koder."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ASSISTANT_WORKFLOW_PATH = ".github/workflows/koder.yml"
REVIEW_WORKFLOW_PATH = ".github/workflows/koder-review.yml"
DEFAULT_SECRET_ENV = "KODER_API_KEY"
REQUIRED_GH_SCOPES = ("repo", "workflow")

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


ASSISTANT_WORKFLOW = """name: Koder Assistant

on:
  workflow_dispatch:
    inputs:
      prompt:
        description: Prompt for Koder
        required: true
        type: string

permissions:
  contents: read
  issues: read
  pull-requests: read

jobs:
  koder:
    runs-on: ubuntu-latest
    env:
      KODER_API_KEY: ${{ secrets.KODER_API_KEY }}
      KODER_MODEL: ${{ vars.KODER_MODEL }}
      KODER_BASE_URL: ${{ vars.KODER_BASE_URL }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Run Koder
        env:
          KODER_PROMPT: ${{ inputs.prompt }}
        run: |
          uvx --from koder koder --print "$KODER_PROMPT" | tee "$GITHUB_STEP_SUMMARY"
"""

REVIEW_WORKFLOW = """name: Koder Pull Request Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: read

jobs:
  koder-review:
    if: ${{ github.event.pull_request.head.repo.fork == false }}
    runs-on: ubuntu-latest
    env:
      KODER_API_KEY: ${{ secrets.KODER_API_KEY }}
      KODER_MODEL: ${{ vars.KODER_MODEL }}
      KODER_BASE_URL: ${{ vars.KODER_BASE_URL }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v5
      - name: Build review prompt
        run: |
          git fetch origin "${{ github.base_ref }}" --depth=1
          git diff --no-ext-diff --unified=80 FETCH_HEAD...HEAD > /tmp/koder-pr.diff
          {
            echo "Review this pull request diff for high-confidence bugs, regressions, and security issues."
            echo "Keep the review concise and include file paths when useful."
            echo
            cat /tmp/koder-pr.diff
          } > /tmp/koder-review-prompt.md
      - name: Run Koder review
        run: |
          uvx --from koder koder --print "$(cat /tmp/koder-review-prompt.md)" | tee "$GITHUB_STEP_SUMMARY"
"""

WORKFLOW_CONTENT = {
    "assistant": (ASSISTANT_WORKFLOW_PATH, ASSISTANT_WORKFLOW),
    "review": (REVIEW_WORKFLOW_PATH, REVIEW_WORKFLOW),
}

USAGE = """Usage: /install-github-app status|plan|apply owner/repo options

Commands:
  status                         Inspect local gh, auth, repo, and workflow files.
  plan owner/repo                Show the Koder GitHub Actions setup plan.
  apply owner/repo options       Create/update workflow files on a setup branch.

Apply options:
  --workflow assistant           Include the manual prompt workflow.
  --workflow review              Include the pull-request review workflow.
  --secret-env NAME              Read the Actions secret value from this env var.
  --skip-secret                  Write workflows without setting an Actions secret.
  --model MODEL                  Set repository variable KODER_MODEL.
  --base-url URL                 Set repository variable KODER_BASE_URL.
  --branch NAME                  Use an existing or new setup branch name.
  --open                         Open the pull-request compare URL after setup.
""".strip()


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class SetupOptions:
    repo: str | None = None
    workflows: tuple[str, ...] = ("assistant", "review")
    secret_env: str = DEFAULT_SECRET_ENV
    skip_secret: bool = False
    model: str | None = None
    base_url: str | None = None
    branch: str | None = None
    open_pr: bool = False


@dataclass(frozen=True)
class ParseResult:
    options: SetupOptions | None = None
    error: str | None = None


@dataclass
class SetupContext:
    repo: str
    branch: str
    default_branch: str
    workflow_paths: list[str] = field(default_factory=list)
    secret_name: str | None = None
    variables: list[str] = field(default_factory=list)
    compare_url: str = ""
    opened: bool = False


def normalize_repo_slug(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    elif "github.com" in text:
        match = re.search(r"github\.com[:/]([^\s?#]+/[^\s?#]+)", text)
        if not match:
            return None
        text = match.group(1)
    text = text.removesuffix(".git").strip("/")
    return text if _REPO_RE.match(text) else None


def detect_current_repo(cwd: Path | None = None) -> str | None:
    result = _run(["git", "remote", "get-url", "origin"], cwd=cwd, timeout=10)
    if result.returncode != 0:
        return None
    return normalize_repo_slug(result.stdout.strip())


def render_github_actions_command(args: list[str], *, cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    if not args or args[0] == "status":
        return render_github_actions_status(cwd=cwd)
    if args[0] in {"help", "--help", "-h"}:
        return USAGE
    if args[0] == "plan":
        parsed = _parse_options(args[1:])
        if parsed.error:
            return f"{parsed.error}\n\n{USAGE}"
        return render_github_actions_plan(parsed.options or SetupOptions(), cwd=cwd)
    if args[0] == "apply":
        parsed = _parse_options(args[1:])
        if parsed.error:
            return f"{parsed.error}\n\n{USAGE}"
        return apply_github_actions_setup(parsed.options or SetupOptions(), cwd=cwd)

    repo = normalize_repo_slug(args[0])
    if repo and len(args) == 1:
        return render_github_actions_plan(SetupOptions(repo=repo), cwd=cwd)
    return USAGE


def render_github_actions_status(*, cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    repo = detect_current_repo(cwd)
    assistant_exists = (cwd / ASSISTANT_WORKFLOW_PATH).exists()
    review_exists = (cwd / REVIEW_WORKFLOW_PATH).exists()
    lines = [
        "github_actions:",
        "command: /install-github-app",
        "integration_scope: local gh CLI + GitHub Actions workflow",
        f"repo: {repo or 'not detected'}",
        *_gh_status_lines(cwd=cwd),
        "local_workflows:",
        f"- {ASSISTANT_WORKFLOW_PATH}: {'present' if assistant_exists else 'missing'}",
        f"- {REVIEW_WORKFLOW_PATH}: {'present' if review_exists else 'missing'}",
        "setup_commands:",
        "- /install-github-app plan owner/repo",
        "- /install-github-app apply owner/repo --secret-env KODER_API_KEY",
    ]
    return "\n".join(lines)


def render_github_actions_plan(options: SetupOptions, *, cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    repo = _resolve_repo(options.repo, cwd)
    workflows = _workflow_names(options.workflows)
    secret_state = "skipped" if options.skip_secret else _env_state(options.secret_env)
    lines = [
        "github_actions: plan",
        "integration_scope: local gh CLI + GitHub Actions workflow",
        f"repo: {repo or 'not detected'}",
        *_gh_status_lines(cwd=cwd),
        "workflows:",
    ]
    for workflow_name in workflows:
        path, _content = WORKFLOW_CONTENT[workflow_name]
        lines.append(f"- {workflow_name}: {path}")
    lines.extend(
        [
            f"secret_env: {options.secret_env if not options.skip_secret else 'skipped'}",
            f"secret_env_status: {secret_state}",
            f"model_variable: {options.model or 'unchanged'}",
            f"base_url_variable: {options.base_url or 'unchanged'}",
            f"branch: {options.branch or 'auto-generated on apply'}",
            "mutation: none",
        ]
    )
    lines.append(f"apply_command: {_render_apply_command(repo or 'owner/repo', options)}")
    return "\n".join(lines)


def apply_github_actions_setup(options: SetupOptions, *, cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    repo = _resolve_repo(options.repo, cwd)
    if not repo:
        return _blocked("repo", "No GitHub repository detected. Pass owner/repo explicitly.")
    if shutil.which("gh") is None:
        return _blocked("gh", "gh CLI not found. Install GitHub CLI from https://cli.github.com")
    if not options.skip_secret and options.secret_env not in os.environ:
        return _blocked(
            "secret",
            f"Environment variable {options.secret_env} is not set. Set it or pass --skip-secret.",
        )

    auth = _run(["gh", "auth", "status", "-h", "github.com"], cwd=cwd, timeout=15)
    if auth.returncode != 0:
        return _blocked("auth", (auth.stderr or auth.stdout or "gh auth status failed").strip())
    missing_scopes = parse_missing_scopes(auth.stdout + "\n" + auth.stderr)
    if missing_scopes:
        return _blocked(
            "auth",
            "GitHub CLI is missing required scopes: "
            + ", ".join(missing_scopes)
            + ". Run: gh auth refresh -h github.com -s repo,workflow",
        )

    admin = _gh(["api", f"repos/{repo}", "--jq", ".permissions.admin"], cwd=cwd)
    if admin.returncode != 0:
        return _gh_blocked("repo", admin)
    if admin.stdout.strip().lower() != "true":
        return _blocked(
            "repo",
            f"Admin access is required to set Actions secrets and workflows for {repo}.",
        )

    default_branch_result = _gh(["api", f"repos/{repo}", "--jq", ".default_branch"], cwd=cwd)
    if default_branch_result.returncode != 0:
        return _gh_blocked("default_branch", default_branch_result)
    default_branch = default_branch_result.stdout.strip()

    sha_result = _gh(
        ["api", f"repos/{repo}/git/ref/heads/{default_branch}", "--jq", ".object.sha"],
        cwd=cwd,
    )
    if sha_result.returncode != 0:
        return _gh_blocked("branch_sha", sha_result)
    base_sha = sha_result.stdout.strip()

    branch = options.branch or f"koder-github-actions-{int(time.time())}"
    branch_result = _gh(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repo}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={base_sha}",
        ],
        cwd=cwd,
    )
    branch_output = branch_result.stdout + branch_result.stderr
    if branch_result.returncode != 0 and "Reference already exists" not in branch_output:
        return _gh_blocked("create_branch", branch_result)

    context = SetupContext(repo=repo, branch=branch, default_branch=default_branch)
    for workflow_name in _workflow_names(options.workflows):
        path, content = WORKFLOW_CONTENT[workflow_name]
        write_result = _put_workflow(
            repo=repo,
            branch=branch,
            path=path,
            content=content,
            message=f"Add Koder {workflow_name} workflow",
            cwd=cwd,
        )
        if write_result.returncode != 0:
            return _gh_blocked(f"write_{workflow_name}_workflow", write_result)
        context.workflow_paths.append(path)

    if not options.skip_secret:
        secret_value = os.environ[options.secret_env]
        secret_result = _gh(
            ["secret", "set", DEFAULT_SECRET_ENV, "--repo", repo],
            cwd=cwd,
            input_text=secret_value,
            timeout=30,
        )
        if secret_result.returncode != 0:
            return _gh_blocked("set_secret", secret_result)
        context.secret_name = DEFAULT_SECRET_ENV

    for variable_name, value in (
        ("KODER_MODEL", options.model),
        ("KODER_BASE_URL", options.base_url),
    ):
        if not value:
            continue
        variable_result = _gh(
            ["variable", "set", variable_name, "--body", value, "--repo", repo],
            cwd=cwd,
            timeout=30,
        )
        if variable_result.returncode != 0:
            return _gh_blocked(f"set_{variable_name.lower()}", variable_result)
        context.variables.append(variable_name)

    context.compare_url = _compare_url(repo, default_branch, branch)
    if options.open_pr:
        context.opened = webbrowser.open(context.compare_url)
    return _render_apply_success(context)


def parse_missing_scopes(output: str) -> list[str] | None:
    match = re.search(r"Token scopes:\s*(.*)$", output, flags=re.MULTILINE)
    if not match:
        return None
    scopes = {scope.strip().strip("'") for scope in match.group(1).split(",")}
    return [scope for scope in REQUIRED_GH_SCOPES if scope not in scopes]


def _parse_options(tokens: list[str]) -> ParseResult:
    repo: str | None = None
    workflows: list[str] = []
    secret_env = DEFAULT_SECRET_ENV
    skip_secret = False
    model: str | None = None
    base_url: str | None = None
    branch: str | None = None
    open_pr = False

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--workflow":
            index += 1
            if index >= len(tokens) or tokens[index] not in WORKFLOW_CONTENT:
                return ParseResult(error="Expected workflow: assistant or review")
            workflows.append(tokens[index])
        elif token == "--secret-env":
            index += 1
            if index >= len(tokens) or not _ENV_RE.match(tokens[index]):
                return ParseResult(
                    error="Expected uppercase environment variable name after --secret-env"
                )
            secret_env = tokens[index]
        elif token == "--skip-secret":
            skip_secret = True
        elif token == "--model":
            index += 1
            if index >= len(tokens) or not tokens[index].strip():
                return ParseResult(error="Expected model name after --model")
            model = tokens[index]
        elif token == "--base-url":
            index += 1
            if index >= len(tokens) or not tokens[index].strip():
                return ParseResult(error="Expected URL after --base-url")
            base_url = tokens[index]
        elif token == "--branch":
            index += 1
            if index >= len(tokens) or not _BRANCH_RE.match(tokens[index]):
                return ParseResult(error="Expected branch name after --branch")
            branch = tokens[index]
        elif token == "--open":
            open_pr = True
        elif token.startswith("--"):
            return ParseResult(error=f"Unknown option: {token}")
        elif repo is None:
            normalized = normalize_repo_slug(token)
            if not normalized:
                return ParseResult(error=f"Invalid GitHub repository: {token}")
            repo = normalized
        else:
            return ParseResult(error=f"Unexpected argument: {token}")
        index += 1

    return ParseResult(
        options=SetupOptions(
            repo=repo,
            workflows=tuple(workflows or ("assistant", "review")),
            secret_env=secret_env,
            skip_secret=skip_secret,
            model=model,
            base_url=base_url,
            branch=branch,
            open_pr=open_pr,
        )
    )


def _workflow_names(workflows: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(workflows or ("assistant", "review")))


def _resolve_repo(repo: str | None, cwd: Path) -> str | None:
    return normalize_repo_slug(repo) or detect_current_repo(cwd)


def _env_state(name: str) -> str:
    return "configured" if os.environ.get(name) else "missing"


def _gh_status_lines(*, cwd: Path) -> list[str]:
    gh_path = shutil.which("gh")
    if gh_path is None:
        return ["gh: not found", "auth: unavailable"]
    version = _run(["gh", "--version"], cwd=cwd, timeout=10)
    version_output = (version.stdout or version.stderr).splitlines()
    version_line = version_output[0] if version.returncode == 0 and version_output else gh_path
    auth = _run(["gh", "auth", "status", "-h", "github.com"], cwd=cwd, timeout=15)
    if auth.returncode != 0:
        auth_state = "not authenticated"
    else:
        missing = parse_missing_scopes(auth.stdout + "\n" + auth.stderr)
        auth_state = "ok" if not missing else "missing scopes: " + ", ".join(missing)
    return [f"gh: {version_line}", f"auth: {auth_state}"]


def _put_workflow(
    *, repo: str, branch: str, path: str, content: str, message: str, cwd: Path
) -> CommandResult:
    sha_result = _gh(["api", f"repos/{repo}/contents/{path}?ref={branch}", "--jq", ".sha"], cwd=cwd)
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    args = [
        "api",
        "--method",
        "PUT",
        f"repos/{repo}/contents/{path}",
        "-f",
        f"message={message}",
        "-f",
        f"content={encoded}",
        "-f",
        f"branch={branch}",
    ]
    if sha_result.returncode == 0 and sha_result.stdout.strip():
        args.extend(["-f", f"sha={sha_result.stdout.strip()}"])
    return _gh(args, cwd=cwd, timeout=30)


def _compare_url(repo: str, default_branch: str, branch: str) -> str:
    title = quote("Add Koder GitHub Actions workflows")
    body = quote("Adds Koder manual prompt and pull-request review workflows.")
    return (
        f"https://github.com/{repo}/compare/{default_branch}...{branch}"
        f"?quick_pull=1&title={title}&body={body}"
    )


def _render_apply_command(repo: str, options: SetupOptions) -> str:
    parts = ["/install-github-app", "apply", repo]
    workflows = _workflow_names(options.workflows)
    if workflows != ("assistant", "review"):
        for workflow in workflows:
            parts.extend(["--workflow", workflow])
    if options.skip_secret:
        parts.append("--skip-secret")
    else:
        parts.extend(["--secret-env", options.secret_env])
    if options.model:
        parts.extend(["--model", options.model])
    if options.base_url:
        parts.extend(["--base-url", options.base_url])
    if options.branch:
        parts.extend(["--branch", options.branch])
    return " ".join(parts)


def _render_apply_success(context: SetupContext) -> str:
    lines = [
        "github_actions: applied",
        f"repo: {context.repo}",
        f"branch: {context.branch}",
        f"default_branch: {context.default_branch}",
        "workflows:",
    ]
    lines.extend(f"- {path}: written" for path in context.workflow_paths)
    lines.append(f"secret: {context.secret_name or 'skipped'}")
    if context.variables:
        lines.append("variables:")
        lines.extend(f"- {name}: set" for name in context.variables)
    else:
        lines.append("variables: unchanged")
    lines.extend(
        [
            f"compare_url: {context.compare_url}",
            f"opened: {'yes' if context.opened else 'no'}",
            "next_step: open compare_url and create the pull request",
        ]
    )
    return "\n".join(lines)


def _blocked(step: str, message: str) -> str:
    return f"github_actions: blocked\nstep: {step}\nmessage: {message}"


def _gh_blocked(step: str, result: CommandResult) -> str:
    output = (result.stderr or result.stdout or "gh command failed").strip()
    return (
        f"github_actions: blocked\nstep: {step}\nexit_code: {result.returncode}\nmessage: {output}"
    )


def _gh(
    args: list[str], *, cwd: Path | None = None, input_text: str | None = None, timeout: int = 30
) -> CommandResult:
    return _run(["gh", *args], cwd=cwd, input_text=input_text, timeout=timeout)


def _run(
    args: list[str], *, cwd: Path | None = None, input_text: str | None = None, timeout: int = 30
) -> CommandResult:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            input=input_text,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(returncode=127, stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "")
    return CommandResult(result.returncode, result.stdout, result.stderr)
