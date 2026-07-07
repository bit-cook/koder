"""Static shell command classification for permission decisions."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

READ_ONLY_COMMANDS = {
    "cat",
    "column",
    "cut",
    "diff",
    "echo",
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

# config flags that only read values; any other form of ``git config`` (a bare
# ``key value`` assignment, ``--global user.email x``, ``core.hooksPath ...``)
# is a write and must require approval.
_GIT_CONFIG_READ_FLAGS = {
    "--get",
    "--get-all",
    "--get-regexp",
    "--get-urlmatch",
    "--list",
    "-l",
}
# config options that take no value and are safe to ignore when deciding whether
# an assignment is present (scope/type selectors, not the operation itself).
_GIT_CONFIG_SCOPE_FLAGS = {
    "--global",
    "--system",
    "--local",
    "--worktree",
    "-z",
    "--null",
    "--name-only",
    "--show-origin",
    "--show-scope",
}
# tag flags that force a write even without a positional tag name.
_GIT_TAG_WRITE_FLAGS = {"-a", "-s", "-m", "-F", "--annotate", "--sign", "--message", "--file"}
# tag flags that indicate read-only listing.
_GIT_TAG_READ_FLAGS = {"-l", "--list", "-n", "--contains", "--points-at", "--sort", "--format"}

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


def _git_config_is_read_only(rest: list[str]) -> bool:
    """``git config`` is read-only only when it reads a value and sets nothing.

    A get/list flag must be present, and there must be no positional assignment
    (a bare ``key`` lookup after ``--get`` is fine, but ``key value`` or a bare
    ``key value`` with no read flag is a write). This blocks the
    ``git config core.hooksPath ...`` privilege-escalation vector.
    """
    if not rest:
        # Bare ``git config`` opens an editor / is not a pure read; treat as write.
        return False

    has_read_flag = any(token in _GIT_CONFIG_READ_FLAGS for token in rest)
    if not has_read_flag:
        return False

    # Any unset/replace/rename operation is a write even alongside a read flag.
    if any(token in GIT_WRITE_FLAGS["config"] for token in rest):
        return False

    # A read flag plus at most one positional (the key to look up) is read-only;
    # a second positional is a value assignment -> write.
    positionals = [
        token
        for token in rest
        if not token.startswith("-")
        and token not in _GIT_CONFIG_READ_FLAGS
        and token not in _GIT_CONFIG_SCOPE_FLAGS
    ]
    return len(positionals) <= 1


def _git_tag_is_read_only(rest: list[str]) -> bool:
    """``git tag`` is read-only only for listing.

    Bare ``git tag`` lists tags. ``-l``/``--list`` (and other listing flags)
    keep it read-only even with a pattern argument. Any create/delete flag
    (``-a``/``-s``/``-m``/``-F``/``-d``/``--force`` ...) or a bare positional
    tag name with no listing flag is a write.
    """
    if any(token in _GIT_TAG_WRITE_FLAGS for token in rest):
        return False
    if any(token in GIT_WRITE_FLAGS["tag"] for token in rest):
        return False
    has_listing_flag = any(token in _GIT_TAG_READ_FLAGS for token in rest)
    has_positional = any(not token.startswith("-") for token in rest)
    # A positional with no listing flag names a tag to create -> write.
    if has_positional and not has_listing_flag:
        return False
    return True


def is_readonly_git_subcommand(tokens: list[str]) -> bool:
    """Check whether a tokenized git command is read-only.

    Examines flags and sub-subcommands to distinguish read-only invocations
    (e.g. ``git branch -a``) from write operations (e.g. ``git branch -D feat``).
    """
    if len(tokens) < 2 or tokens[0] != "git":
        return False

    subcommand = tokens[1]
    rest = tokens[2:]

    # ``--output[=]FILE`` (git's diff-machinery flag, accepted by log/show/
    # diff/...) writes/truncates an arbitrary path, so any otherwise read-only
    # subcommand carrying it must require approval. A stray ``-o FILE`` is
    # likewise treated as a write, but only for the core read-only subcommands:
    # on extended subcommands such as ``ls-files``, ``-o`` means ``--others``
    # and is a legitimate read-only flag.
    if any(token == "--output" or token.startswith("--output=") for token in rest):
        return False
    if subcommand in READ_ONLY_GIT_SUBCOMMANDS and "-o" in rest:
        return False

    # Pure read-only subcommands that have no write flags
    if subcommand in READ_ONLY_GIT_SUBCOMMANDS and subcommand not in GIT_WRITE_FLAGS:
        return True

    # Subcommands with per-flag write rules
    if subcommand in GIT_WRITE_FLAGS:
        write_flags = GIT_WRITE_FLAGS[subcommand]

        # ``config`` and ``tag`` need value-assignment analysis, not just a flag
        # scan: a bare assignment such as ``git config key value`` has no write
        # flag yet still mutates state (and can rewrite hooksPath).
        if subcommand == "config":
            return _git_config_is_read_only(rest)
        if subcommand == "tag":
            return _git_tag_is_read_only(rest)

        # Check for known read-only sub-subcommands first
        readonly_subs = GIT_READONLY_SUB_SUBCOMMANDS.get(subcommand, set())
        if rest and rest[0] in readonly_subs:
            return True

        # ``stash``/``remote``/``notes``/``worktree`` are read-only ONLY via an
        # explicit read-only sub-subcommand. A bare ``git stash`` (== stash push)
        # or bare ``git remote`` add-form must not be auto-allowed. ``remote``
        # additionally accepts ``-v``/``--verbose`` as a listing flag.
        if subcommand in {"stash", "remote", "notes", "worktree"}:
            if (
                subcommand == "remote"
                and rest
                and all(token in {"-v", "--verbose"} for token in rest)
            ):
                return True
            return False

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

# Command-runner wrappers: they run whatever command follows them, so they can
# never be treated as read-only on their own. Their own options / VAR=val
# assignments are stripped and the inner command is classified recursively.
# (``env`` was previously misfiled in READ_ONLY_COMMANDS, so ``env rm -rf ~/data``
# classified as read-only.)
COMMAND_RUNNER_PREFIXES = {
    "command",
    "env",
    "nice",
    "nohup",
    "setsid",
    "stdbuf",
    "timeout",
    "xargs",
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

# A write redirection to a file: ``>``/``>>``, optionally fd-prefixed (``1>``,
# ``2>>``) — a digit before ``>`` picks the stream but still truncates/appends
# the target file, so it MUST count as a write (mirrors bash_security's
# ``_REDIRECT_RE``). Exemptions: ``/dev/null`` (discard) and fd duplication /
# closing (``2>&1``, ``>&2``, ``2>&-``), which retarget a descriptor rather
# than write a file. Other ``/dev/*`` targets are caught earlier as dangerous.
WRITE_REDIRECTION_PATTERN = re.compile(r"\d*>>?(?!\s*/dev/null\b)(?!&(?:\d|-))")
COMMAND_SPLIT_PATTERN = re.compile(r"\s*(?:\|\||&&|[|;])\s*")

# Shell operators that separate command segments during quote-aware tokenization.
# ``|&`` (pipe-both-streams) is a separator too: ``cat a |& rm b`` runs ``rm b``.
_SEGMENT_SEPARATORS = {"|", "||", "&&", ";", ";;", "&", "|&"}
# Redirection-style operator tokens emitted by shlex punctuation_chars mode; these
# are not command words and must not be classified as such.
_REDIRECTION_TOKENS = {">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>"}


def _tokenize_segments(raw: str) -> list[list[str]]:
    """Split a command into per-segment token lists, honoring shell quoting.

    Newlines separate whole commands (``ls\\nrm -rf foo`` is two commands, not
    one), so the raw input is split on line boundaries FIRST — ``shlex`` treats
    ``\\n`` as ordinary whitespace and would otherwise merge the lines into a
    single segment classified only by the first word.

    Within each line, uses ``shlex`` with ``punctuation_chars`` so operators
    like ``|`` and ``&&`` inside quotes (e.g. ``grep "a\\|b"``) stay part of
    their word instead of being treated as segment separators. Raises
    ``ValueError`` on genuinely malformed input (e.g. unbalanced quotes).
    """
    segments: list[list[str]] = []
    # splitlines() handles \n, \r\n and \r; each physical line is its own
    # command chain and must be classified independently.
    for line in raw.splitlines():
        if not line.strip():
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)

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


# find actions that mutate the filesystem, execute commands, or write to a
# caller-supplied file (the whole ``-f*`` output family truncates its target).
_FIND_MUTATING_FLAGS = {
    "-delete",
    "-exec",
    "-execdir",
    "-fls",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-ok",
    "-okdir",
}

# Runner options that consume a SEPARATE following argument (so the argument is
# not mistaken for the inner command). All other ``-`` tokens are treated as
# boolean flags that consume only themselves; ``--opt=value`` always consumes
# only itself. Being conservative here is safe: a mis-parsed runner falls back
# to an approval prompt rather than auto-allowing.
_RUNNER_VALUE_OPTIONS: dict[str, set[str]] = {
    "timeout": {"-k", "--kill-after", "-s", "--signal"},
    "nice": {"-n", "--adjustment"},
    "setsid": set(),
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(),
    "xargs": {"-I", "-i", "-n", "-P", "-d", "-E", "-L", "-s", "--replace", "--max-args"},
    "nohup": set(),
}
# Runners that take a mandatory leading positional before the inner command
# (``timeout DURATION cmd``). That positional must be skipped so the inner
# command — not the duration — is classified.
_RUNNER_LEADING_POSITIONALS = {"timeout": 1}


def _normalize_command_name(token: str) -> str:
    """Return the basename of ``argv[0]`` so absolute/relative paths match.

    ``/usr/bin/sudo`` and ``./sudo`` must be recognized as ``sudo`` before the
    privileged/runner/read-only lookups; otherwise a full path silently bypasses
    the classifier (an absolute-path privilege escalation).
    """
    if not token:
        return token
    return os.path.basename(token)


def _resolve_runner_segment(tokens: list[str]) -> tuple[list[str], bool]:
    """Strip command-runner wrappers, returning the effective inner command.

    ``env rm -rf x``, ``timeout 5 rm x``, ``nice -n 10 make`` all delegate to an
    inner command that must be classified in place of the wrapper. Returns
    ``(inner_tokens, resolvable)``. ``resolvable`` is False when a runner wraps
    nothing (e.g. bare ``env``) so callers can require approval instead of
    treating the bare runner as safe.
    """
    # Bound the recursion; nested runners (``env timeout 5 rm``) are rare but
    # legitimate, while an unbounded chain is not worth trusting.
    for _ in range(8):
        if not tokens:
            return tokens, False
        name = _normalize_command_name(tokens[0])
        if name not in COMMAND_RUNNER_PREFIXES:
            # Normalize argv[0] in place so downstream checks see the basename.
            return [name, *tokens[1:]], True

        value_options = _RUNNER_VALUE_OPTIONS.get(name, set())
        leading_positionals = _RUNNER_LEADING_POSITIONALS.get(name, 0)
        rest = tokens[1:]
        idx = 0
        while idx < len(rest):
            token = rest[idx]
            # Leading VAR=value assignments (env-style) are not the inner command.
            if "=" in token and not token.startswith("-"):
                idx += 1
                continue
            if token.startswith("-"):
                # Long option with attached value (``--signal=TERM``) consumes
                # only itself; an option taking a separate argument consumes the
                # next token too; everything else is a boolean flag.
                if "=" not in token and token in value_options:
                    idx += 2
                else:
                    idx += 1
                continue
            break
        # Skip mandatory leading positionals (e.g. timeout's DURATION).
        for _pos in range(leading_positionals):
            if idx < len(rest) and not rest[idx].startswith("-"):
                idx += 1
        inner = rest[idx:]
        if not inner:
            # Runner with no inner command (bare ``env``, ``env VAR=val``):
            # nothing concrete to classify -> not resolvable, require approval.
            return [], False
        tokens = inner
    # Recursion bound exceeded: refuse to auto-trust a deep runner chain.
    return tokens, False


def normalize_segment_for_rule(tokens: list[str]) -> str | None:
    """Return the effective-command string for a tokenized segment, or ``None``.

    Strips leading ``VAR=val`` assignments and known safe command-runner
    wrappers (``env``/``timeout``/``nice``/``nohup``/``stdbuf``/``setsid``/... and
    their leading args) down to the inner command that actually runs, then joins
    the resulting tokens back into a whitespace-normalized string suitable for
    prefix rule matching. This lets a prefix rule like ``npm test:*`` generalize
    across ``FOO=bar npm test`` and ``env npm test --watch``.

    Reuses the Wave-1 :func:`_resolve_runner_segment` resolver so there is a
    single stripper of record (no second, divergent normalizer). Returns
    ``None`` when the segment resolves to nothing concrete (e.g. a bare ``env``
    with no inner command), when it is empty, or when normalization would not
    change the segment (so callers can cheaply skip the extra match).
    """
    if not tokens:
        return None
    # Strip leading bare ``VAR=val`` assignments (``FOO=bar npm test``): these are
    # environment-prefix assignments, not the command. The runner resolver only
    # strips assignments that follow a wrapper token (``env FOO=bar ...``), so a
    # bare leading assignment is handled here before delegating. This is NOT a
    # second runner stripper — it only drops the ``NAME=value`` prefix, then the
    # Wave-1 :func:`_resolve_runner_segment` does all wrapper resolution.
    stripped = list(tokens)
    while stripped and "=" in stripped[0] and not stripped[0].startswith("-"):
        stripped.pop(0)
    if not stripped:
        # Only assignments, no command (e.g. ``FOO=bar``): nothing concrete.
        return None
    inner, _resolvable = _resolve_runner_segment(stripped)
    # A wrapper that wrapped nothing concrete (bare ``env``) has no effective
    # command to normalize; leave it to the raw-string match / classifier.
    if not inner:
        return None
    normalized = " ".join(inner)
    if not normalized or normalized == " ".join(tokens):
        # Nothing was stripped: the raw target already equals the normalized one.
        return None
    return normalized


def _sort_writes_output(tokens: list[str]) -> bool:
    """Return True if any ``sort`` token requests writing to an output file.

    ``sort -o FILE`` / ``sort --output=FILE`` overwrite FILE, so they are
    writes even though plain ``sort`` is read-only. Covers the separate
    (``-o FILE``), attached (``-oFILE``), and clustered (``-uo FILE``) short
    forms plus both long forms. Any short-option cluster containing ``o`` is
    treated as a write; over-matching is safe (falls back to approval).
    """
    for token in tokens[1:]:
        if token == "--output" or token.startswith("--output="):
            return True
        if token.startswith("-") and not token.startswith("--") and "o" in token:
            return True
    return False


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
    command = _normalize_command_name(tokens[0])
    if command == "git":
        return is_readonly_git_subcommand([command, *tokens[1:]])
    if command == "sed":
        # sed is read-only unless it writes in place; handled specially because
        # its classification is flag-dependent (see _sed_is_in_place).
        return not _sed_is_in_place(tokens)
    if command == "find":
        return not any(token in _FIND_MUTATING_FLAGS for token in tokens)
    if command == "sort":
        # sort is read-only unless -o/--output overwrites a file.
        return not _sort_writes_output(tokens)
    return command in READ_ONLY_COMMANDS


def _is_write_segment(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command = _normalize_command_name(tokens[0])
    if command == "git":
        return not _is_read_only_segment(tokens)
    if command == "sed":
        # In-place sed mutates files; flag-dependent, so handled specially.
        return _sed_is_in_place(tokens)
    if command == "find":
        return any(token in _FIND_MUTATING_FLAGS for token in tokens)
    if command == "sort":
        # sort -o/--output overwrites its target file.
        return _sort_writes_output(tokens)
    return command in WRITE_COMMANDS


def _is_privileged_segment(tokens: list[str], lowered_command: str) -> bool:
    """Segments that are hard-denied: privilege escalation or destructive deletes."""
    if not tokens:
        return False
    # Normalize argv[0] so ``/usr/bin/sudo`` and ``./sudo`` are caught, not just
    # a bare ``sudo`` token (absolute-path privilege bypass).
    command = _normalize_command_name(tokens[0])

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
    command = _normalize_command_name(tokens[0])

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

    # Resolve command-runner wrappers (env/timeout/xargs/...) down to the inner
    # command they execute, so ``env rm -rf x`` is classified as ``rm``, not as a
    # read-only ``env``. A runner that wraps nothing (bare ``env``) is not
    # resolvable and must fall through to an approval prompt.
    resolved_segments: list[list[str]] = []
    all_resolvable = True
    for tokens in tokenized_segments:
        inner, resolvable = _resolve_runner_segment(tokens)
        if not resolvable:
            all_resolvable = False
        if inner:
            resolved_segments.append(inner)

    if any(_is_privileged_segment(tokens, lowered) for tokens in resolved_segments):
        return ShellCommandDecision(
            command=command,
            allowed=False,
            read_only=False,
            requires_approval=True,
            destructive=True,
            malformed=False,
            reason="dangerous command prefix detected",
        )

    if any(_is_code_execution_segment(tokens) for tokens in resolved_segments):
        return ShellCommandDecision(
            command=command,
            allowed=True,
            read_only=False,
            requires_approval=True,
            destructive=False,
            malformed=False,
            reason="command executes arbitrary code; requires approval",
        )

    read_only = all_resolvable and all(
        _is_read_only_segment(tokens) for tokens in resolved_segments
    )
    mutates_filesystem = any(_is_write_segment(tokens) for tokens in resolved_segments) or bool(
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
