"""Static shell command classification for permission decisions."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

READ_ONLY_COMMANDS = {
    "cat",
    "column",
    "cut",
    "diff",
    "echo",
    "env",
    "file",
    "find",
    "git",
    "grep",
    "head",
    "jq",
    "ls",
    "md5sum",
    "nl",
    "od",
    "paste",
    "pwd",
    "rg",
    "sed",
    "sha1sum",
    "sha256sum",
    "sort",
    "stat",
    "strings",
    "tail",
    "tr",
    "uniq",
    "wc",
    "which",
}

READ_ONLY_GIT_SUBCOMMANDS = {
    "branch",
    "diff",
    "log",
    "reflog",
    "rev-parse",
    "show",
    "status",
}

# Flags that make otherwise read-only git subcommands into write operations
GIT_WRITE_FLAGS: dict[str, set[str]] = {
    "branch": {
        "-d",
        "-D",
        "--delete",
        "-m",
        "-M",
        "--move",
        "-c",
        "-C",
        "--copy",
        "--set-upstream-to",
        "--unset-upstream",
        "--edit-description",
    },
    "stash": {"pop", "drop", "apply", "push", "save", "clear", "create", "store"},
    "tag": {"-d", "--delete", "-f", "--force"},
    "remote": {"add", "remove", "rm", "rename", "set-url", "set-head", "prune"},
    "config": {
        "--unset",
        "--unset-all",
        "--remove-section",
        "--rename-section",
        "--replace-all",
    },
    "notes": {"add", "append", "copy", "edit", "merge", "remove", "prune"},
    "worktree": {"add", "remove", "prune", "move", "repair", "lock", "unlock"},
}

# Sub-subcommands that are definitely read-only for flag-checked subcommands
GIT_READONLY_SUB_SUBCOMMANDS: dict[str, set[str]] = {
    "stash": {"list", "show"},
    "remote": {"show", "get-url"},
    "worktree": {"list"},
    "notes": {"list", "show"},
    "config": {"--get", "--get-all", "--get-regexp", "--list", "-l"},
}

# Extended set of always-read-only git subcommands
EXTENDED_READ_ONLY_GIT_SUBCOMMANDS = (
    READ_ONLY_GIT_SUBCOMMANDS
    | GIT_WRITE_FLAGS.keys()
    | {
        "blame",
        "shortlog",
        "describe",
        "ls-files",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "name-rev",
        "for-each-ref",
        "count-objects",
        "fsck",
        "verify-pack",
        "verify-commit",
        "verify-tag",
        "whatchanged",
    }
)

# Branch flags that indicate read-only listing (not positional branch names)
_BRANCH_READ_FLAGS = {"-a", "--all", "-r", "--remotes", "-v", "--verbose", "-vv", "--list"}


def is_readonly_git_subcommand(tokens: list[str]) -> bool:
    """Check whether a tokenized git command is read-only.

    Examines flags and sub-subcommands to distinguish read-only invocations
    (e.g. ``git branch -a``) from write operations (e.g. ``git branch -D feat``).
    """
    if len(tokens) < 2 or tokens[0] != "git":
        return False

    subcommand = tokens[1]

    # Pure read-only subcommands that have no write flags
    if subcommand in READ_ONLY_GIT_SUBCOMMANDS and subcommand not in GIT_WRITE_FLAGS:
        return True

    # Subcommands with per-flag write rules
    if subcommand in GIT_WRITE_FLAGS:
        rest = tokens[2:]
        write_flags = GIT_WRITE_FLAGS[subcommand]

        # Check for known read-only sub-subcommands first
        readonly_subs = GIT_READONLY_SUB_SUBCOMMANDS.get(subcommand, set())
        if rest and rest[0] in readonly_subs:
            return True

        # Check if any write flag is present
        if any(token in write_flags for token in rest):
            return False

        # Special case for "branch": positional args mean branch creation
        if subcommand == "branch":
            for token in rest:
                if not token.startswith("-") and token not in _BRANCH_READ_FLAGS:
                    return False

        return True

    # Extended read-only subcommands (blame, ls-files, etc.)
    if subcommand in EXTENDED_READ_ONLY_GIT_SUBCOMMANDS:
        return True

    return False


WRITE_COMMANDS = {
    "chmod",
    "chown",
    "cp",
    "git",
    "mkdir",
    "mv",
    "rm",
    "rmdir",
    "sed",
    "tee",
    "touch",
}

DANGEROUS_PREFIXES = {
    "bash",
    "bun",
    "bunx",
    "deno",
    "eval",
    "exec",
    "fish",
    "lua",
    "node",
    "npm",
    "npx",
    "perl",
    "php",
    "python",
    "python2",
    "python3",
    "ruby",
    "sh",
    "ssh",
    "sudo",
    "tsx",
    "yarn",
    "zsh",
}

DANGEROUS_PATTERNS = [
    re.compile(r">\s*/dev/(?!null\b)", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE),
]

WRITE_REDIRECTION_PATTERN = re.compile(r"(?:^|[^\d])>>?(?!\s*/dev/null\b)")
COMMAND_SPLIT_PATTERN = re.compile(r"\s*(?:\|\||&&|[|;])\s*")


@dataclass(frozen=True)
class ShellCommandDecision:
    """Static safety classification for a shell command."""

    command: str
    allowed: bool
    read_only: bool
    requires_approval: bool
    destructive: bool
    malformed: bool
    reason: str


def _is_read_only_segment(tokens: list[str]) -> bool:
    if not tokens:
        return True
    command = tokens[0]
    if command == "git":
        return is_readonly_git_subcommand(tokens)
    if command == "sed":
        return "-i" not in tokens and not any(token.startswith("-i") for token in tokens)
    return command in READ_ONLY_COMMANDS


def _is_write_segment(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command = tokens[0]
    if command == "git":
        return not _is_read_only_segment(tokens)
    if command == "sed":
        return "-i" in tokens or any(token.startswith("-i") for token in tokens)
    return command in WRITE_COMMANDS


def _is_dangerous_segment(tokens: list[str], lowered_command: str) -> bool:
    if not tokens:
        return False
    command = tokens[0]

    if command in DANGEROUS_PREFIXES:
        if command == "git" and len(tokens) > 1 and tokens[1] in READ_ONLY_GIT_SUBCOMMANDS:
            return False
        if command == "npm" and len(tokens) > 1 and tokens[1] not in {"run", "exec"}:
            return False
        if command in {"yarn", "bun"} and len(tokens) > 1 and tokens[1] not in {"run", "exec"}:
            return False
        return True

    if command in {"rm", "rmdir"} and re.search(
        r"\brm\b.*(?:-rf|-fr).*(?:^|[ /])/(?:\s|$)", lowered_command
    ):
        return True

    return False


def classify_shell_command(command: str) -> ShellCommandDecision:
    """Classify a shell command into read-only, mutating, or blocked states."""
    raw = command.strip()
    if not raw:
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=False,
            destructive=False,
            malformed=True,
            reason="empty command",
        )

    lowered = raw.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(raw):
            return ShellCommandDecision(
                command=command,
                allowed=False,
                read_only=False,
                requires_approval=True,
                destructive=True,
                malformed=False,
                reason="dangerous command pattern detected",
            )

    try:
        segments = [segment for segment in COMMAND_SPLIT_PATTERN.split(raw) if segment.strip()]
        tokenized_segments = [shlex.split(segment, posix=True) for segment in segments]
    except ValueError:
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=False,
            destructive=False,
            malformed=True,
            reason="command could not be parsed",
        )

    if not tokenized_segments or not any(tokens for tokens in tokenized_segments):
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=False,
            destructive=False,
            malformed=True,
            reason="empty command",
        )

    if any(_is_dangerous_segment(tokens, lowered) for tokens in tokenized_segments):
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=True,
            destructive=True,
            malformed=False,
            reason="dangerous command prefix detected",
        )

    read_only = all(_is_read_only_segment(tokens) for tokens in tokenized_segments)
    mutates_filesystem = any(_is_write_segment(tokens) for tokens in tokenized_segments) or bool(
        WRITE_REDIRECTION_PATTERN.search(raw)
    )

    if read_only and not mutates_filesystem:
        return ShellCommandDecision(
            command=command,
            allowed=True,
            read_only=True,
            requires_approval=False,
            destructive=False,
            malformed=False,
            reason="read-only command",
        )

    return ShellCommandDecision(
        command=command,
        allowed=True,
        read_only=False,
        requires_approval=True,
        destructive=False,
        malformed=False,
        reason="command may mutate filesystem or execute code",
    )
