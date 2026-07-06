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
    "tee",
    "touch",
}

# Privilege-escalation commands: hard-denied, never routed to an approval prompt.
PRIVILEGED_PREFIXES = {
    "doas",
    "su",
    "sudo",
}

# Interpreters and script runners: they execute arbitrary code so they always
# require approval, but they are everyday dev tools (pytest, build scripts,
# node tooling) and must stay approvable rather than hard-denied.
CODE_EXECUTION_PREFIXES = {
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

# Shell operators that separate command segments during quote-aware tokenization.
_SEGMENT_SEPARATORS = {"|", "||", "&&", ";", ";;", "&"}
# Redirection-style operator tokens emitted by shlex punctuation_chars mode; these
# are not command words and must not be classified as such.
_REDIRECTION_TOKENS = {">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>", "|&"}


def _tokenize_segments(raw: str) -> list[list[str]]:
    """Split a command into per-segment token lists, honoring shell quoting.

    Uses ``shlex`` with ``punctuation_chars`` so operators like ``|`` and ``&&``
    inside quotes (e.g. ``grep "a\\|b"``) stay part of their word instead of
    being treated as segment separators. Raises ``ValueError`` on genuinely
    malformed input (e.g. unbalanced quotes).
    """
    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        if token in _REDIRECTION_TOKENS or (token and all(ch in ";&|<>" for ch in token)):
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


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


# find actions that mutate the filesystem or execute commands
_FIND_MUTATING_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf"}


def _sed_is_in_place(tokens: list[str]) -> bool:
    """Return True if any ``sed`` token requests in-place editing (a write).

    Covers short forms ``-i`` and ``-i.bak`` (via ``startswith("-i")``) plus GNU
    long forms ``--in-place`` and ``--in-place=SUFFIX`` (which do not match
    ``startswith("-i")``, so the bare long form must be matched explicitly).
    """
    return any(
        token.startswith("-i") or token == "--in-place" or token.startswith("--in-place=")
        for token in tokens
    )


def _is_read_only_segment(tokens: list[str]) -> bool:
    if not tokens:
        return True
    command = tokens[0]
    if command == "git":
        return is_readonly_git_subcommand(tokens)
    if command == "sed":
        # sed is read-only unless it writes in place; handled specially because
        # its classification is flag-dependent (see _sed_is_in_place).
        return not _sed_is_in_place(tokens)
    if command == "find":
        return not any(token in _FIND_MUTATING_FLAGS for token in tokens)
    return command in READ_ONLY_COMMANDS


def _is_write_segment(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command = tokens[0]
    if command == "git":
        return not _is_read_only_segment(tokens)
    if command == "sed":
        # In-place sed mutates files; flag-dependent, so handled specially.
        return _sed_is_in_place(tokens)
    return command in WRITE_COMMANDS


def _is_privileged_segment(tokens: list[str], lowered_command: str) -> bool:
    """Segments that are hard-denied: privilege escalation or destructive deletes."""
    if not tokens:
        return False
    command = tokens[0]

    if command in PRIVILEGED_PREFIXES:
        return True

    if command in {"rm", "rmdir"} and re.search(
        r"\brm\b.*(?:-rf|-fr).*(?:^|[ /])/(?:\s|$)", lowered_command
    ):
        return True

    return False


def _is_code_execution_segment(tokens: list[str]) -> bool:
    """Segments that run arbitrary code: allowed, but always need approval."""
    if not tokens:
        return False
    command = tokens[0]

    if command not in CODE_EXECUTION_PREFIXES:
        return False
    if command == "npm" and len(tokens) > 1 and tokens[1] not in {"run", "exec"}:
        return False
    if command in {"yarn", "bun"} and len(tokens) > 1 and tokens[1] not in {"run", "exec"}:
        return False
    return True


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
        tokenized_segments = _tokenize_segments(raw)
    except ValueError:
        # Unparseable syntax (e.g. unbalanced quotes) is not proof of danger;
        # fall back to manual approval instead of hard-denying the call.
        return ShellCommandDecision(
            command=command,
            allowed=True,
            read_only=False,
            requires_approval=True,
            destructive=False,
            malformed=True,
            reason="command could not be parsed; requires manual approval",
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

    if any(_is_privileged_segment(tokens, lowered) for tokens in tokenized_segments):
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=True,
            destructive=True,
            malformed=False,
            reason="dangerous command prefix detected",
        )

    if any(_is_code_execution_segment(tokens) for tokens in tokenized_segments):
        return ShellCommandDecision(
            command=command,
            allowed=True,
            read_only=False,
            requires_approval=True,
            destructive=False,
            malformed=False,
            reason="command executes arbitrary code; requires approval",
        )

    read_only = all(_is_read_only_segment(tokens) for tokens in tokenized_segments)
    mutates_filesystem = any(_is_write_segment(tokens) for tokens in tokenized_segments) or bool(
        WRITE_REDIRECTION_PATTERN.search(raw)
    )

    # Command/process substitution can smuggle arbitrary commands into an
    # otherwise read-only line; never auto-allow those.
    has_substitution = "$(" in raw or "`" in raw or "<(" in raw or ">(" in raw

    if read_only and not mutates_filesystem and not has_substitution:
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
