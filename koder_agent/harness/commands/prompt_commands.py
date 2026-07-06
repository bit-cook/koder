"""First-class discovery of standalone ``.koder/commands/*.md`` prompt commands.

Unlike skills (which live under ``.koder/skills`` and are model-invocable tools),
prompt commands are lightweight markdown files that act as reusable slash-command
prompt templates. A file ``deploy.md`` becomes the ``/deploy`` command whose body
is dispatched to the model as a prompt, after ``$ARGUMENTS`` / ``$0``.. expansion
(positional placeholders are 0-indexed, matching the skill loader).

Discovery scans two locations (project overrides user on a name collision):

* ``~/.koder/commands/*.md`` (user)
* ``<cwd>/.koder/commands/*.md`` (project)

Markdown files may carry optional YAML frontmatter with ``description``,
``argument-hint`` and ``allowed-tools``; the remaining body is the prompt
template. Rendering intentionally reimplements a small ``$ARGUMENTS`` / ``$N``
substitution locally so the skill loader is left untouched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from koder_agent.harness.paths import project_commands_dir, user_commands_dir

logger = logging.getLogger(__name__)

# Mirror the skill loader's frontmatter regex so behavior is consistent.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)$", re.DOTALL)

# Mirror the skill loader's positional-arg regex so multi-digit indices resolve
# correctly. A naive ascending ``str.replace`` loop turns ``$10`` into ``<value
# of $1>0``; matching the whole ``\d+`` in one pass avoids that.
_POSITIONAL_ARG_RE = re.compile(r"\$ARGUMENTS\[(?P<bracket>\d+)\]|\$(?P<bare>\d+)")


def _parse_list(value: Any) -> Optional[list[str]]:
    """Normalize an ``allowed-tools`` value into a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
        return [item for item in items if item]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


@dataclass
class PromptCommand:
    """A markdown-defined slash command whose body is a prompt template."""

    name: str
    description: str
    body: str
    source: str = "project"
    command_path: Optional[Path] = None
    argument_hint: Optional[str] = None
    allowed_tools: Optional[list[str]] = None

    def render_prompt(self, args: list[str]) -> str:
        """Expand ``$ARGUMENTS`` / ``$N`` placeholders in the template body.

        This mirrors :meth:`koder_agent.tools.skill.Skill.render_prompt` for the
        argument-substitution portion only, deliberately kept local so the skill
        loader is never touched.
        """
        final = self.body
        joined = " ".join(args).strip()

        # Substitute positional placeholders in a single regex pass so multi-digit
        # indices resolve correctly (``$10`` maps to args[10], not ``<args[1]>0``).
        def _positional(match: re.Match[str]) -> str:
            index = int(match.group("bracket") or match.group("bare"))
            if 0 <= index < len(args):
                return args[index]
            return match.group(0)

        final = _POSITIONAL_ARG_RE.sub(_positional, final)

        if "$ARGUMENTS" in final:
            final = final.replace("$ARGUMENTS", joined)
        elif joined:
            final = f"{final.rstrip()}\n\nARGUMENTS: {joined}"

        return final.strip()


def _command_default_name(path: Path) -> str:
    return path.stem


def _load_command(path: Path, *, source: str) -> Optional[PromptCommand]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read prompt command %s: %s", path, exc)
        return None

    text = raw.lstrip("﻿")
    meta: dict[str, Any] = {}
    body = text

    match = _FRONTMATTER_RE.match(text)
    if match:
        body = match.group("body")
        try:
            loaded = yaml.safe_load(match.group("yaml")) or {}
            if isinstance(loaded, dict):
                meta = loaded
            else:
                logger.warning("Frontmatter in %s must be a mapping", path)
        except yaml.YAMLError as exc:
            logger.warning("invalid YAML in %s: %s", path, exc)

    name = str(meta.get("name") or _command_default_name(path))
    description = str(meta.get("description") or "")

    argument_hint_raw = meta.get("argument-hint")
    if isinstance(argument_hint_raw, list):
        argument_hint: Optional[str] = "[" + " ".join(str(i) for i in argument_hint_raw) + "]"
    elif argument_hint_raw is not None:
        argument_hint = str(argument_hint_raw)
    else:
        argument_hint = None

    tools_raw = meta["allowed-tools"] if "allowed-tools" in meta else meta.get("allowed_tools")
    allowed_tools = _parse_list(tools_raw)
    if tools_raw == []:
        allowed_tools = []

    return PromptCommand(
        name=name,
        description=description,
        body=body.lstrip("\n"),
        source=source,
        command_path=path,
        argument_hint=argument_hint,
        allowed_tools=allowed_tools,
    )


def _discover_dir(directory: Path, *, source: str) -> dict[str, PromptCommand]:
    if not directory.exists() or not directory.is_dir():
        return {}
    commands: dict[str, PromptCommand] = {}
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        command = _load_command(path, source=source)
        if command is not None:
            commands[command.name] = command
    return commands


def discover_prompt_commands(cwd: str | Path | None = None) -> dict[str, PromptCommand]:
    """Discover user + project prompt commands (project wins on collision)."""
    current_cwd = Path(cwd or Path.cwd())

    merged: dict[str, PromptCommand] = {}
    # User first, then project overrides on name collision.
    merged.update(_discover_dir(user_commands_dir(), source="user"))
    merged.update(_discover_dir(project_commands_dir(current_cwd), source="project"))
    return merged
