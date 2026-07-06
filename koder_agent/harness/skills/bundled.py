"""Bundled skills that ship with koder, loaded from markdown files.

Each bundled skill lives in ``bundled_skills/<name>.md`` next to this module,
with YAML frontmatter declaring ``name``, ``description``, and optionally
``argument_hint`` and ``disable_model_invocation``. The markdown body is the
skill prompt; ``$ARGUMENTS`` is substituted with user arguments at runtime.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from koder_agent.tools.skill import Skill

logger = logging.getLogger(__name__)

BUNDLED_SKILLS_DIR = Path(__file__).parent / "bundled_skills"

# Same frontmatter shape as SkillLoader.FRONTMATTER_RE in koder_agent.tools.skill
# (duplicated here because tools.skill imports this module).
_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)$", re.DOTALL)


@dataclass(frozen=True)
class BundledSkillDefinition:
    name: str
    description: str
    content: str
    argument_hint: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    execution_context: str | None = None
    agent: str | None = None


def _meta_get(meta: dict[str, Any], key: str) -> Any:
    """Read a frontmatter key, accepting underscore or hyphen spelling."""
    if key in meta:
        return meta[key]
    return meta.get(key.replace("_", "-"))


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_skill_file(path: Path) -> BundledSkillDefinition | None:
    """Parse one bundled skill markdown file, returning None if malformed."""
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError as exc:
        logger.warning("Failed to read bundled skill %s: %s", path, exc)
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.warning("Bundled skill %s has no YAML frontmatter; skipping", path)
        return None

    try:
        meta = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML frontmatter in bundled skill %s: %s", path, exc)
        return None
    if not isinstance(meta, dict):
        logger.warning("Frontmatter in bundled skill %s must be a mapping; skipping", path)
        return None

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description:
        logger.warning("Bundled skill %s is missing name or description; skipping", path)
        return None

    argument_hint = _meta_get(meta, "argument_hint")
    return BundledSkillDefinition(
        name=name,
        description=description,
        content=match.group("body").lstrip("\n"),
        argument_hint=str(argument_hint) if argument_hint is not None else None,
        disable_model_invocation=_parse_bool(_meta_get(meta, "disable_model_invocation")),
        user_invocable=_parse_bool(_meta_get(meta, "user_invocable"), default=True),
    )


def _definitions() -> list[BundledSkillDefinition]:
    if not BUNDLED_SKILLS_DIR.is_dir():
        logger.warning("Bundled skills directory not found: %s", BUNDLED_SKILLS_DIR)
        return []

    definitions: list[BundledSkillDefinition] = []
    for path in sorted(BUNDLED_SKILLS_DIR.glob("*.md")):
        definition = _parse_skill_file(path)
        if definition is not None:
            definitions.append(definition)
    return definitions


def get_bundled_skills() -> dict[str, Skill]:
    from koder_agent.tools.skill import Skill

    bundled: dict[str, Skill] = {}
    for definition in _definitions():
        bundled[definition.name] = Skill(
            name=definition.name,
            description=definition.description,
            content=definition.content,
            source="bundled",
            disable_model_invocation=definition.disable_model_invocation,
            user_invocable=definition.user_invocable,
            argument_hint=definition.argument_hint,
            execution_context=definition.execution_context,
            agent=definition.agent,
            base_dir=BUNDLED_SKILLS_DIR,
        )
    return bundled
