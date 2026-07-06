"""Bash command security analysis.

Detects dangerous patterns in shell commands including:
- Output redirections to sensitive system paths
- Device file redirections (blocks /dev/sda etc., allows /dev/null and friends)
- Heredoc injections targeting sensitive files
- Pipe-to-interpreter chains (curl|bash, wget|sh, base64 -d|bash)
- Privilege escalation (chmod u+s, chown root + chmod u+s)
- Eval with variable expansion
- Disk destructive commands (dd of=/dev/, mkfs)
- Fork bombs
- Recursive rm targeting a protected root (/, /*, ~, $HOME, top-level system
  dirs, or the cwd after `cd /`), in any flag order
- Dangerous system commands (shutdown, reboot, halt, poweroff, init 0/6)
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Sensitive paths -- writes to these are always blocked
# ---------------------------------------------------------------------------

SENSITIVE_PATHS: set[str] = {
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/hosts",
    "/etc/crontab",
    "/etc/ssh/sshd_config",
    "/etc/ld.so.preload",
    "/etc/pam.d",
    "/etc/security",
}

# Patterns that match sensitive path *prefixes* (for subtrees)
_SENSITIVE_PATH_PREFIXES: tuple[str, ...] = (
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/hosts",
    "/etc/crontab",
    "/etc/ssh/",
    "/etc/ld.so.",
    "/etc/pam.d/",
    "/etc/security/",
    "/var/spool/cron",
)

# Home-relative sensitive paths (matched with ~ or $HOME prefix)
_HOME_SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".ssh/",
    ".ssh/authorized_keys",
    ".ssh/config",
    ".ssh/known_hosts",
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".gitconfig",
    ".config/git/",
)

# Safe device files that are okay to redirect to
_SAFE_DEVICE_FILES: set[str] = {
    "/dev/null",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
}

_SAFE_DEVICE_PREFIXES: tuple[str, ...] = ("/dev/fd/",)

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Redirect target extraction: matches >, >>, 2>, 2>>, &>, etc. followed by path
_REDIRECT_RE = re.compile(r"(?:\d*)>>?\s*([^\s;|&]+)")  # captures the target path

# Heredoc with redirect: << DELIM > /path  or  tee /path << DELIM
_HEREDOC_REDIRECT_RE = re.compile(r"<<-?\s*['\"]?\w+['\"]?\s*>>?\s*([^\s;|&]+)")

# tee command targeting a path: sudo tee /path << or tee /path
_TEE_TARGET_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;|&<>]+)")

# Pipe to interpreter: curl|bash, wget|sh, base64 -d|bash, etc.
_PIPE_TO_INTERPRETER_RE = re.compile(
    r"\|\s*(?:bash|sh|zsh|dash|ksh|fish|python[23]?|perl|ruby|node)\b"
)

# Fork bomb variants
_FORK_BOMB_RE = re.compile(
    r":\(\)\s*\{.*\|\s*:.*&.*\}\s*;?\s*:|" r"\.\(\)\s*\{.*\|\s*\..*&.*\}\s*;?\s*\."
)

# dd writing to device
_DD_DEVICE_RE = re.compile(r"\bdd\b.*\bof=/dev/")

# mkfs command
_MKFS_RE = re.compile(r"\bmkfs\b")

# Dangerous deletion targets. Recursive rm against any of these (bare, or with a
# trailing slash / glob) wipes the whole filesystem or the user's home dir.
# NOTE: `$HOME`/`${HOME}` and `~` all expand to the home directory at runtime;
# we treat them as dangerous roots without needing to know the actual value.
_RM_DANGEROUS_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/*",
        "~",
        "~/",
        "~/*",
        "$HOME",
        "${HOME}",
    }
)

# Cwd-relative targets ("." / "./" / "*" / "./*") are only dangerous when the
# working directory has been changed to a dangerous root earlier in the same
# compound command (e.g. `cd / && rm -rf .`). On their own they are common,
# legitimate operations (clearing the current project dir).
_RM_CWD_TARGETS: frozenset[str] = frozenset({".", "./", "*", "./*"})

# Top-level system directories whose recursive deletion is catastrophic.
_RM_DANGEROUS_SYSTEM_DIRS: frozenset[str] = frozenset(
    {
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib32",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/srv",
        "/sys",
        "/usr",
        "/var",
    }
)

# Command separators used to split a compound command into individual segments so
# each `rm` invocation can be analyzed on its own (e.g. `cd / && rm -rf .`).
_SEGMENT_SPLIT_RE = re.compile(r"(?:\|\||&&|[;|&\n])")

# Leading `VAR=value` assignments that may precede a command word.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# chmod u+s (setuid)
_CHMOD_SUID_RE = re.compile(r"\bchmod\s+[a-z+]*[ugo]*\+s\b")

# chown root
_CHOWN_ROOT_RE = re.compile(r"\bchown\s+root\b")

# eval with variable expansion
_EVAL_VAR_RE = re.compile(r"\beval\s+.*\$")

# Device file redirect (not to safe device)
_DEV_REDIRECT_RE = re.compile(r"(?:\d*)>>?\s*/dev/\S+")

# Dangerous system commands.
# Only match when the dangerous word is in *command position*: at the start of
# the string, after a command separator (; | & newline), or after sudo/doas.
# This avoids false positives like `echo "reboot done"` where the word appears
# as a quoted argument. A leading optional `sudo`/`doas` (with optional flags)
# is consumed so `sudo reboot` still matches.
_CMD_START = r"(?:^|[;|&\n])\s*(?:(?:sudo|doas)(?:\s+-\w+)*\s+)?"
_SYSTEM_CMDS_RE = re.compile(_CMD_START + r"(?:(?:shutdown|reboot|halt|poweroff)\b|init\s+[06]\b)")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BashSecurityAnalysis:
    """Result of analyzing a bash command for security issues."""

    blocked: bool = False
    reason: str = ""
    has_dangerous_redirect: bool = False
    has_sensitive_path_write: bool = False
    has_pipe_to_interpreter: bool = False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _is_sensitive_path(path: str) -> bool:
    """Check if a path targets a sensitive location."""
    # Normalize: expand ~ to a placeholder for matching
    normalized = path.strip("'\"")

    # Check home-relative sensitive paths
    if normalized.startswith("~") or normalized.startswith("$HOME"):
        # Strip the home prefix using proper prefix removal (NOT lstrip, which
        # strips any leading chars in the set, mangling paths like ~/Music).
        suffix = normalized.removeprefix("$HOME").removeprefix("~")
        suffix = suffix.removeprefix("/")
        for pattern in _HOME_SENSITIVE_PATTERNS:
            if suffix == pattern or suffix.startswith(pattern):
                return True

    # Check absolute sensitive paths
    for prefix in _SENSITIVE_PATH_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix):
            return True

    return False


def _is_dangerous_device(path: str) -> bool:
    """Check if a path is a dangerous device file."""
    normalized = path.strip("'\"")
    if not normalized.startswith("/dev/"):
        return False
    if normalized in _SAFE_DEVICE_FILES:
        return False
    for prefix in _SAFE_DEVICE_PREFIXES:
        if normalized.startswith(prefix):
            return False
    return True


def has_dangerous_redirect(command: str) -> tuple[bool, str]:
    """Check for dangerous output redirections."""
    for m in _REDIRECT_RE.finditer(command):
        target = m.group(1)
        if _is_dangerous_device(target):
            return True, f"Redirect to dangerous device: {target}"
        if _is_sensitive_path(target):
            return True, f"Redirect to sensitive path: {target}"
    return False, ""


def has_dangerous_heredoc(command: str) -> tuple[bool, str]:
    """Check for heredoc injections targeting sensitive files."""
    # Pattern 1: << DELIM > /sensitive/path
    for m in _HEREDOC_REDIRECT_RE.finditer(command):
        target = m.group(1)
        if _is_sensitive_path(target):
            return True, f"Heredoc redirect to sensitive path: {target}"

    # Pattern 2: tee /sensitive/path << DELIM
    for m in _TEE_TARGET_RE.finditer(command):
        target = m.group(1)
        if _is_sensitive_path(target):
            return True, f"Tee write to sensitive path: {target}"

    return False, ""


def has_sensitive_path_write(command: str) -> bool:
    """Check if the command writes to a sensitive path (via any mechanism)."""
    # Check redirects
    for m in _REDIRECT_RE.finditer(command):
        if _is_sensitive_path(m.group(1)):
            return True
    # Check heredoc redirects
    for m in _HEREDOC_REDIRECT_RE.finditer(command):
        if _is_sensitive_path(m.group(1)):
            return True
    # Check tee
    for m in _TEE_TARGET_RE.finditer(command):
        if _is_sensitive_path(m.group(1)):
            return True
    return False


def _collapses_to_root(target: str) -> bool:
    """Return True if an absolute path is equivalent to '/' after collapsing.

    GNU coreutils treats ``//``, ``/.``, ``/..``, ``/../..`` etc. as the root
    filesystem, and ``rm -rf /.`` slips past the literal ``--preserve-root``
    guard. Any absolute path whose lexical resolution lands back at ``/`` is a
    whole-filesystem wipe — including paths with real components that are later
    cancelled by ``..`` (e.g. ``/tmp/..``, ``/usr/local/../..``). ``..`` at or
    above root stays at root (matching the shell), so a real component followed
    by an equal-or-greater number of ``..`` collapses to ``/``.
    """
    if not target.startswith("/"):
        return False
    # Resolve the path lexically against root: '.' and '' are no-ops, '..' pops
    # the stack (clamped at root), any other component pushes. Collapses to root
    # iff the stack is empty at the end.
    stack: list[str] = []
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
            # '..' at root stays at root (shell semantics); nothing to pop.
            continue
        stack.append(part)
    return not stack


def _normalize_rm_target(target: str) -> str:
    """Normalize an rm target for dangerous-root comparison.

    Strips surrounding whitespace and quotes, then collapses root-equivalent
    absolute paths (``//``, ``/.``, ``/..``) to ``/`` so they are caught by the
    dangerous-root set. Trailing-slash and glob variants are handled by the
    caller (`_rm_target_is_dangerous`).
    """
    t = target.strip().strip("'\"")
    if _collapses_to_root(t):
        return "/"
    return t


def _rm_target_is_dangerous(target: str) -> bool:
    """Return True if an rm target refers to a filesystem/home root."""
    t = _normalize_rm_target(target)
    if not t:
        return False
    if t in _RM_DANGEROUS_ROOTS:
        return True
    # A trailing slash variant of a dangerous root (e.g. "/", "$HOME/", "${HOME}/")
    if t != "/" and t.endswith("/") and t.rstrip("/") in _RM_DANGEROUS_ROOTS:
        return True
    # Home dir with a wildcard: "~/*", "$HOME/*", "${HOME}/*"
    for home in ("~", "$HOME", "${HOME}"):
        if t == f"{home}/*" or t == f"{home}*":
            return True
    # Top-level system directories, with optional trailing slash or glob.
    stripped = t.rstrip("/")
    if stripped in _RM_DANGEROUS_SYSTEM_DIRS:
        return True
    if stripped.endswith("/*") and stripped[:-2] in _RM_DANGEROUS_SYSTEM_DIRS:
        return True
    return False


def _tokenize_segment(segment: str) -> list[str]:
    """Tokenize a single command segment, tolerant of shell-invalid input."""
    try:
        # posix=False keeps quote characters, but we want them stripped for path
        # comparison; posix=True splits the way a shell would. Malformed quoting
        # (unbalanced quotes) raises ValueError -- fall back to naive split.
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _skip_command_prefix(tokens: list[str]) -> int:
    """Return the index of the command word, skipping env assignments/sudo/doas."""
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGN_RE.match(tokens[idx]):
        idx += 1
    if idx < len(tokens) and tokens[idx] in ("sudo", "doas"):
        idx += 1
        while idx < len(tokens) and tokens[idx].startswith("-"):
            idx += 1
    return idx


def _analyze_rm_deletion(command: str) -> tuple[bool, str]:
    """Detect a recursive `rm` targeting a dangerous filesystem/home root.

    Splits the compound command into segments, finds `rm` invocations (allowing a
    leading sudo/doas and env-var assignments), parses their flags for recursion
    (-r/-R/--recursive, including combined short flags like -rf) and blocks when a
    dangerous root is among the targets.

    Recursive deletion of a dangerous root is blocked whether or not `--force`
    is present, since the destructive potential is identical; `--force` only
    suppresses the interactive prompt.

    Cwd-relative targets (".", "./", "*") are only treated as dangerous when an
    earlier segment in the same compound command changed the working directory to
    a dangerous root (e.g. `cd / && rm -rf .`). This preserves the very common
    legitimate `rm -rf .` / `rm -rf *` inside a project directory.
    """
    cwd_is_dangerous = False

    for raw_segment in _SEGMENT_SPLIT_RE.split(command):
        segment = raw_segment.strip()
        if not segment:
            continue
        tokens = _tokenize_segment(segment)
        if not tokens:
            continue

        idx = _skip_command_prefix(tokens)
        if idx >= len(tokens):
            continue
        cmd_word = tokens[idx]
        args = tokens[idx + 1 :]

        # Track `cd` into a dangerous root so a later cwd-relative rm is caught.
        if cmd_word == "cd":
            for arg in args:
                if arg.startswith("-"):
                    continue
                normalized = _normalize_rm_target(arg)
                if (
                    normalized in _RM_DANGEROUS_ROOTS
                    or normalized.rstrip("/") in _RM_DANGEROUS_SYSTEM_DIRS
                ):
                    cwd_is_dangerous = True
                break
            continue

        # Accept `rm` or an absolute path to rm (e.g. /bin/rm).
        if cmd_word != "rm" and not cmd_word.endswith("/rm"):
            continue

        recursive = False
        targets: list[str] = []
        options_done = False
        for tok in args:
            if not options_done and tok == "--":
                options_done = True
                continue
            if not options_done and tok.startswith("--"):
                name = tok[2:]
                if name == "recursive":
                    recursive = True
                # Other long options (e.g. --force, --verbose) don't change the
                # danger classification.
                continue
            if not options_done and tok.startswith("-") and len(tok) > 1:
                # Combined short flags: -r, -f, -rf, -fr, -Rf, etc.
                flags = tok[1:]
                if "r" in flags or "R" in flags:
                    recursive = True
                continue
            targets.append(tok)

        if not recursive:
            continue
        for target in targets:
            normalized = _normalize_rm_target(target)
            if _rm_target_is_dangerous(target):
                return (
                    True,
                    f"Recursive deletion targeting a protected root ({normalized})",
                )
            if cwd_is_dangerous and normalized in _RM_CWD_TARGETS:
                return (
                    True,
                    "Recursive deletion of the current directory after cd into a "
                    f"protected root ({normalized})",
                )

    return False, ""


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------


def analyze_command(command: str) -> BashSecurityAnalysis:
    """Analyze a shell command for security threats.

    Returns a BashSecurityAnalysis with blocked=True and a reason
    if the command is considered dangerous, otherwise blocked=False.
    """
    if not command or not command.strip():
        return BashSecurityAnalysis()

    # --- Fork bomb ---
    if _FORK_BOMB_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Fork bomb detected",
        )

    # --- rm targeting a dangerous root (recursive) ---
    rm_dangerous, rm_reason = _analyze_rm_deletion(command)
    if rm_dangerous:
        return BashSecurityAnalysis(
            blocked=True,
            reason=rm_reason,
        )

    # --- dd to device ---
    if _DD_DEVICE_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Disk-destructive dd command targeting device file",
            has_dangerous_redirect=True,
        )

    # --- mkfs ---
    if _MKFS_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Filesystem formatting command (mkfs)",
        )

    # --- Pipe to interpreter ---
    if _PIPE_TO_INTERPRETER_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Pipe to interpreter detected -- remote code execution risk",
            has_pipe_to_interpreter=True,
        )

    # --- Dangerous redirects ---
    redir_dangerous, redir_reason = has_dangerous_redirect(command)
    if redir_dangerous:
        is_device = "device" in redir_reason.lower()
        is_sensitive = "sensitive" in redir_reason.lower()
        return BashSecurityAnalysis(
            blocked=True,
            reason=redir_reason,
            has_dangerous_redirect=is_device,
            has_sensitive_path_write=is_sensitive,
        )

    # --- Heredoc injection ---
    heredoc_dangerous, heredoc_reason = has_dangerous_heredoc(command)
    if heredoc_dangerous:
        return BashSecurityAnalysis(
            blocked=True,
            reason=heredoc_reason,
            has_sensitive_path_write=True,
        )

    # --- Sensitive path writes (catch-all for tee etc.) ---
    if has_sensitive_path_write(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Write to sensitive system path detected",
            has_sensitive_path_write=True,
        )

    # --- chmod u+s (setuid) ---
    if _CHMOD_SUID_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Setuid bit modification (chmod u+s) -- privilege escalation risk",
        )

    # --- chown root + chmod u+s combo ---
    if _CHOWN_ROOT_RE.search(command) and _CHMOD_SUID_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Privilege escalation: chown root with setuid",
        )

    # --- eval with variable expansion ---
    if _EVAL_VAR_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Eval with variable expansion -- arbitrary code execution risk",
        )

    # --- Dangerous system commands ---
    if _SYSTEM_CMDS_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Dangerous system command (shutdown/reboot/halt/poweroff)",
        )

    return BashSecurityAnalysis()
