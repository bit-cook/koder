"""Local status line setup helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .statusline_settings import update_user_statusline_config

_PS1_RE = re.compile(r'(?:^|\n)\s*(?:export\s+)?PS1\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)

_DYNAMIC_ESCAPES: tuple[tuple[str, str], ...] = (
    (r"\u", '"$(whoami)"'),
    (r"\h", '"$(hostname -s)"'),
    (r"\H", '"$(hostname)"'),
    (r"\w", '"$(pwd)"'),
    (r"\W", '"$(basename "$(pwd)")"'),
    (r"\t", '"$(date +%H:%M:%S)"'),
    (r"\d", '"$(date "+%a %b %d")"'),
    (r"\@", '"$(date +%I:%M%p)"'),
)
_LITERAL_ESCAPES: tuple[tuple[str, str], ...] = (
    (r"\n", r"\n"),
    (r"\$", "$"),
    (r"\#", "#"),
    (r"\!", "!"),
)


@dataclass(frozen=True)
class AutoConfiguredStatusLine:
    """Result of importing a shell prompt into status line settings."""

    source_path: Path
    prompt: str
    command: str
    settings_path: Path


def _prompt_files() -> tuple[Path, ...]:
    return (
        Path("~/.zshrc").expanduser(),
        Path("~/.bashrc").expanduser(),
        Path("~/.bash_profile").expanduser(),
        Path("~/.profile").expanduser(),
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _merge_literal(tokens: list[tuple[str, str]], value: str) -> None:
    if not value:
        return
    if tokens and tokens[-1][0] == "literal":
        tokens[-1] = ("literal", tokens[-1][1] + value)
    else:
        tokens.append(("literal", value))


def _trim_trailing_prompt_marker(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    while tokens and tokens[-1][0] == "literal" and not tokens[-1][1].strip():
        tokens.pop()
    if not tokens:
        return tokens
    kind, value = tokens[-1]
    if kind != "literal":
        return tokens
    stripped = value.rstrip()
    if stripped.endswith("$") or stripped.endswith(">"):
        stripped = stripped[:-1].rstrip()
        if stripped:
            tokens[-1] = ("literal", stripped)
        else:
            tokens.pop()
    return tokens


def translate_prompt_to_command(prompt: str) -> str:
    """Translate a shell PS1 value into a status line shell command."""

    cleaned = prompt.replace(r"\[", "").replace(r"\]", "")
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(cleaned):
        if cleaned.startswith(r"\033[", index) or cleaned.startswith(r"\e[", index):
            terminator = cleaned.find("m", index)
            if terminator != -1:
                fragment = cleaned[index : terminator + 1]
                _merge_literal(tokens, fragment.replace(r"\e", r"\033"))
                index = terminator + 1
                continue
        matched = False
        for escape, command in _DYNAMIC_ESCAPES:
            if cleaned.startswith(escape, index):
                tokens.append(("dynamic", command))
                index += len(escape)
                matched = True
                break
        if matched:
            continue
        for escape, literal in _LITERAL_ESCAPES:
            if cleaned.startswith(escape, index):
                _merge_literal(tokens, literal)
                index += len(escape)
                matched = True
                break
        if matched:
            continue
        if cleaned[index] == "\\" and index + 1 < len(cleaned):
            _merge_literal(tokens, cleaned[index + 1])
            index += 2
            continue
        _merge_literal(tokens, cleaned[index])
        index += 1

    tokens = _trim_trailing_prompt_marker(tokens)
    format_parts: list[str] = []
    args: list[str] = []
    for kind, value in tokens:
        if kind == "dynamic":
            format_parts.append("%s")
            args.append(value)
        else:
            format_parts.append(value.replace("%", "%%"))
    format_string = "".join(format_parts).rstrip()
    if not format_string:
        format_string = "koder"
    command = f"printf {_shell_quote(format_string)}"
    if args:
        command += " " + " ".join(args)
    return command


def auto_configure_statusline_from_shell_prompt() -> AutoConfiguredStatusLine | None:
    """Import the first PS1 definition found in common shell config files."""

    for prompt_file in _prompt_files():
        if not prompt_file.exists():
            continue
        try:
            content = prompt_file.read_text(encoding="utf-8")
        except Exception:
            continue
        match = _PS1_RE.search(content)
        if match is None:
            continue
        prompt = match.group(1)
        command = translate_prompt_to_command(prompt)
        settings_path = update_user_statusline_config(
            {
                "type": "command",
                "command": command,
                "padding": 0,
            }
        )
        return AutoConfiguredStatusLine(
            source_path=prompt_file,
            prompt=prompt,
            command=command,
            settings_path=settings_path,
        )
    return None
