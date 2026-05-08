"""Local gh-backed PR comments command support."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

GH_TIMEOUT_S = 5.0


async def _run_gh(*args: str, timeout: float = GH_TIMEOUT_S, cwd: Path | None = None) -> str | None:
    """Run a gh CLI command and return stdout, or None on failure."""

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            cwd=str((cwd or Path.cwd()).resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return None
        return stdout.decode().strip() if stdout else None
    except FileNotFoundError:
        return None
    except asyncio.TimeoutError:
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return None
    except Exception:
        return None


def _author_login(comment: dict[str, Any]) -> str:
    user = comment.get("user") or {}
    login = user.get("login")
    return str(login) if login else "unknown"


def _quote_block(body: str | None, *, indent: str = "  ") -> list[str]:
    lines = (body or "").splitlines() or ["(no body)"]
    return [f"{indent}> {line}" if line else f"{indent}>" for line in lines]


def _format_review_location(comment: dict[str, Any]) -> str:
    path = comment.get("path")
    line = comment.get("line") or comment.get("original_line")
    if path and line:
        return f"{path}#{line}"
    if path:
        return str(path)
    return "review comment"


def _format_issue_comment(comment: dict[str, Any]) -> list[str]:
    lines = [f"- @{_author_login(comment)} PR conversation:"]
    lines.extend(_quote_block(comment.get("body")))
    return lines


def _format_review_thread(
    comment: dict[str, Any],
    *,
    replies_by_parent: dict[int, list[dict[str, Any]]],
    indent_level: int = 1,
) -> list[str]:
    indent = "  " * max(indent_level - 1, 0)
    quote_indent = "  " * indent_level
    lines: list[str] = []

    if indent_level == 1:
        lines.append(f"- @{_author_login(comment)} {_format_review_location(comment)}:")
    else:
        lines.append(f"{indent}- @{_author_login(comment)}:")

    diff_hunk = comment.get("diff_hunk")
    if diff_hunk:
        lines.append(f"{quote_indent}```diff")
        lines.extend(f"{quote_indent}{line}" for line in str(diff_hunk).splitlines())
        lines.append(f"{quote_indent}```")

    lines.extend(_quote_block(comment.get("body"), indent=quote_indent))

    comment_id = comment.get("id")
    if isinstance(comment_id, int):
        for reply in replies_by_parent.get(comment_id, []):
            lines.extend(
                _format_review_thread(
                    reply,
                    replies_by_parent=replies_by_parent,
                    indent_level=indent_level + 1,
                )
            )
    return lines


def format_pr_comments_markdown(
    issue_comments: list[dict[str, Any]], review_comments: list[dict[str, Any]]
) -> str:
    """Render PR comments in a readable markdown thread format."""

    if not issue_comments and not review_comments:
        return "No comments found."

    reply_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    top_level_reviews: list[dict[str, Any]] = []
    for comment in review_comments:
        parent_id = comment.get("in_reply_to_id")
        if isinstance(parent_id, int):
            reply_map[parent_id].append(comment)
        else:
            top_level_reviews.append(comment)

    sections: list[str] = ["## Comments", ""]
    threads: list[list[str]] = []
    threads.extend(_format_issue_comment(comment) for comment in issue_comments)
    threads.extend(
        _format_review_thread(comment, replies_by_parent=reply_map) for comment in top_level_reviews
    )

    for index, thread_lines in enumerate(threads):
        sections.extend(thread_lines)
        if index != len(threads) - 1:
            sections.append("")
    return "\n".join(sections)


def _parse_json_list(raw: str | None) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [item for item in data if isinstance(item, dict)]


def _parse_pr_metadata(raw: str | None) -> tuple[int, str, str] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    number = data.get("number")
    head_repo = data.get("headRepository") or {}
    owner = (head_repo.get("owner") or {}).get("login")
    repo = head_repo.get("name")
    if not isinstance(number, int) or not owner or not repo:
        return None
    return number, str(owner), str(repo)


async def run_pr_comments(*, cwd: Path | None = None) -> str:
    """Fetch and format comments for the current pull request via gh."""

    repo_cwd = (cwd or Path.cwd()).resolve()
    pr_view = await _run_gh("pr", "view", "--json", "number,headRepository", cwd=repo_cwd)
    metadata = _parse_pr_metadata(pr_view)
    if metadata is None:
        return "pr-comments: unable to resolve current PR via gh."

    number, owner, repo = metadata
    issue_comments = _parse_json_list(
        await _run_gh("api", f"/repos/{owner}/{repo}/issues/{number}/comments", cwd=repo_cwd)
    )
    review_comments = _parse_json_list(
        await _run_gh("api", f"/repos/{owner}/{repo}/pulls/{number}/comments", cwd=repo_cwd)
    )
    if issue_comments is None or review_comments is None:
        return "pr-comments: unable to fetch PR comments via gh."

    return format_pr_comments_markdown(issue_comments, review_comments)
