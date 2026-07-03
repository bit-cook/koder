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
- rm -rf /
- Dangerous system commands (shutdown, reboot, halt, poweroff, init 0/6)
"""

from __future__ import annotations

import re
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

# rm -rf / (with optional -- and various flag orders)
_RM_RF_ROOT_RE = re.compile(
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)"
    r"\s+(/\s|/;|/$|/\)|/\||/&&|/\b)"
)

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

    # --- rm -rf / ---
    if _RM_RF_ROOT_RE.search(command):
        return BashSecurityAnalysis(
            blocked=True,
            reason="Recursive forced deletion of root filesystem",
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
