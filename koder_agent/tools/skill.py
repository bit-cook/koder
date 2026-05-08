"""Skill tool and loader for progressive disclosure of agent skills."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from agents import function_tool
from pydantic import BaseModel

from koder_agent.config import get_config
from koder_agent.harness.paths import harness_home_dir
from koder_agent.harness.skills.bundled import get_bundled_skills
from koder_agent.harness.skills.discovery import discover_skills_for_paths

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9:.-]*[a-z0-9])?$")
SKILL_ADDITIONAL_DIRS_ENV = "KODER_ADDITIONAL_DIRS"
INLINE_COMMAND_RE = re.compile(r"!\`([^`]+)\`")
SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
DEFAULT_CHAR_BUDGET = 8_000
MAX_LISTING_DESC_CHARS = 250


@dataclass
class Skill:
    """A loaded skill with its metadata and content."""

    name: str
    description: str
    content: str
    allowed_tools: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    skill_path: Optional[Path] = None
    source: str = "project"
    disable_model_invocation: bool = False
    user_invocable: bool = True
    argument_hint: str | None = None
    argument_names: list[str] | None = None
    model: str | None = None
    effort: str | int | None = None
    execution_context: str | None = None
    agent: str | None = None
    hooks: dict[str, Any] | None = None
    paths: list[str] | None = None
    shell: str | None = None
    base_dir: Path | None = None
    plugin_name: str | None = None

    def to_prompt(self) -> str:
        lines: list[str] = [
            f"Skill Name: {self.name}",
            f"Description: {self.description}",
        ]
        if self.argument_hint:
            lines.append(f"Argument hint: {self.argument_hint}")
        if self.allowed_tools is not None:
            lines.append(
                "Allowed tools: "
                + (", ".join(self.allowed_tools) if self.allowed_tools else "(none specified)")
            )
        if self.execution_context:
            lines.append(f"Execution context: {self.execution_context}")
        if self.agent:
            lines.append(f"Agent: {self.agent}")
        if self.model:
            lines.append(f"Model: {self.model}")
        if self.effort is not None:
            lines.append(f"Effort: {self.effort}")
        if self.paths:
            lines.append(f"Paths: {', '.join(self.paths)}")
        if self.metadata:
            lines.append("Metadata:")
            for key, value in self.metadata.items():
                lines.append(f"- {key}: {value}")

        lines.append("")
        lines.append("Skill Content:")
        lines.append(self.content.strip())
        return "\n".join(lines).strip()

    def render_prompt(self, args: list[str], *, session_id: str | None = None) -> str:
        final = self.content
        joined = " ".join(args).strip()

        for index, value in enumerate(args):
            final = final.replace(f"$ARGUMENTS[{index}]", value)
            final = final.replace(f"${index}", value)

        if "$ARGUMENTS" in final:
            final = final.replace("$ARGUMENTS", joined)
        elif joined:
            final = f"{final.rstrip()}\n\nARGUMENTS: {joined}"

        if self.base_dir is not None:
            final = final.replace("${KODER_SKILL_DIR}", str(self.base_dir))
        if session_id is not None:
            final = final.replace("${KODER_SESSION_ID}", session_id)

        # Expand plugin env vars for plugin-sourced skills
        if self.source == "plugin" and self.plugin_name and self.base_dir is not None:
            from koder_agent.harness.plugins.env import expand_plugin_vars

            final = expand_plugin_vars(final, self.plugin_name, self.base_dir)

        final = INLINE_COMMAND_RE.sub(lambda m: self._run_inline_command(m.group(1).strip()), final)
        return final.strip()

    def _run_inline_command(self, command: str) -> str:
        if not command:
            return ""
        if self.shell == "powershell":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                cwd=str(Path.cwd()),
                check=False,
            )
        else:
            result = subprocess.run(
                ["/bin/bash", "-lc", command],
                capture_output=True,
                text=True,
                cwd=str(Path.cwd()),
                check=False,
            )
        output = result.stdout.strip()
        if output:
            return output
        return result.stderr.strip()


def _validate_skill_name(name: str, skill_path: Path) -> list[str]:
    warnings: list[str] = []
    if len(name) > MAX_NAME_LENGTH:
        warnings.append(
            f"Skill name '{name}' exceeds {MAX_NAME_LENGTH} characters "
            f"(length: {len(name)}) in {skill_path}"
        )
    if not NAME_PATTERN.match(name):
        warnings.append(
            f"Skill name '{name}' should contain only lowercase letters, "
            f"numbers, and hyphens in {skill_path}"
        )
    return warnings


def _validate_skill_description(description: str, skill_path: Path) -> list[str]:
    warnings: list[str] = []
    if len(description) > MAX_DESCRIPTION_LENGTH:
        warnings.append(
            f"Skill description exceeds {MAX_DESCRIPTION_LENGTH} characters "
            f"(length: {len(description)}) in {skill_path}"
        )
    return warnings


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part]
    return [str(value).strip()]


def _skill_default_name(skill_path: Path) -> str:
    if skill_path.name.lower() == "skill.md":
        return skill_path.parent.name
    return skill_path.stem


def _parse_paths(value: Any) -> list[str] | None:
    parsed = _parse_list(value)
    return parsed or None


def _expand_path(value: str | Path) -> Path:
    text = str(value)
    if text.startswith("~/"):
        return (Path.home() / text[2:]).resolve()
    return Path(text).expanduser().resolve()


def _load_plugin_name(plugin_dir: Path) -> str | None:
    from koder_agent.harness.plugins.manifest import find_manifest

    manifest_path = find_manifest(plugin_dir)
    if manifest_path is None:
        return None
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    name = manifest.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


class SkillLoader:
    """Loader for discovering and parsing skill definitions from SKILL.md files."""

    FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)$", re.DOTALL)
    LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).expanduser()
        self._cache: dict[str, Skill] = {}
        self._discovered = False

    def load_skill(
        self,
        skill_path: Path,
        *,
        source: str = "project",
        plugin_name: str | None = None,
    ) -> Optional[Skill]:
        try:
            raw = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Warning: failed to read skill file {skill_path}: {exc}")
            return None

        text = raw.lstrip("\ufeff")
        match = self.FRONTMATTER_RE.match(text)
        meta: dict[str, Any] = {}
        body = text

        if match:
            yaml_text = match.group("yaml")
            body = match.group("body")
            try:
                loaded = yaml.safe_load(yaml_text) or {}
                if isinstance(loaded, dict):
                    meta = loaded
                else:
                    print(f"Warning: frontmatter in {skill_path} must be a mapping")
            except yaml.YAMLError as exc:
                print(f"Warning: invalid YAML in {skill_path}: {exc}")
        else:
            print(f"Warning: no frontmatter found in {skill_path}")

        resolved_name = str(meta.get("name") or _skill_default_name(skill_path))
        if plugin_name:
            resolved_name = f"{plugin_name}:{resolved_name}"
        description = str(meta.get("description") or "")

        for warning in _validate_skill_name(resolved_name, skill_path):
            print(f"Warning: {warning}")
        for warning in _validate_skill_description(description, skill_path):
            print(f"Warning: {warning}")

        tools_raw = meta["allowed-tools"] if "allowed-tools" in meta else meta.get("allowed_tools")
        allowed_tools = _parse_list(tools_raw)
        if tools_raw == []:
            allowed_tools = []

        reserved = {
            "name",
            "description",
            "allowed_tools",
            "allowed-tools",
            "argument-hint",
            "arguments",
            "disable-model-invocation",
            "user-invocable",
            "model",
            "effort",
            "context",
            "agent",
            "hooks",
            "paths",
            "shell",
        }
        extra = {k: v for k, v in meta.items() if k not in reserved}
        extra_meta = extra if extra else None

        body = body.lstrip("\n")
        base_dir = skill_path.parent
        body = self._resolve_paths(body, base_dir)

        return Skill(
            name=resolved_name,
            description=description,
            content=body,
            allowed_tools=allowed_tools,
            metadata=extra_meta,
            skill_path=skill_path,
            source=source,
            disable_model_invocation=_parse_bool(
                meta.get("disable-model-invocation"), default=False
            ),
            user_invocable=_parse_bool(meta.get("user-invocable"), default=True),
            argument_hint=(
                "[" + " ".join(str(item) for item in meta["argument-hint"]) + "]"
                if isinstance(meta.get("argument-hint"), list)
                else str(meta["argument-hint"])
                if meta.get("argument-hint") is not None
                else None
            ),
            argument_names=_parse_list(meta.get("arguments")),
            model=(
                (
                    "inherit"
                    if str(meta.get("model")).strip().lower() == "inherit"
                    else str(meta.get("model")).strip()
                )
                if meta.get("model") is not None and str(meta.get("model")).strip()
                else None
            ),
            effort=meta.get("effort") if meta.get("effort") is not None else None,
            execution_context="fork" if meta.get("context") == "fork" else None,
            agent=(
                str(meta.get("agent")).strip()
                if meta.get("agent") is not None and str(meta.get("agent")).strip()
                else None
            ),
            hooks=meta.get("hooks") if isinstance(meta.get("hooks"), dict) else None,
            paths=_parse_paths(meta.get("paths")),
            shell=(
                str(meta.get("shell")).strip()
                if meta.get("shell") is not None and str(meta.get("shell")).strip()
                else None
            ),
            base_dir=base_dir,
            plugin_name=plugin_name,
        )

    def discover_skills(
        self, *, source: str = "project", plugin_name: str | None = None
    ) -> list[Skill]:
        self._cache.clear()

        if not self.skills_dir.exists():
            print(f"Warning: skills directory does not exist: {self.skills_dir}")
            self._discovered = True
            return []

        # Load SKILL.md files (standard skills format)
        for skill_file in self.skills_dir.rglob("SKILL.md"):
            skill = self.load_skill(skill_file, source=source, plugin_name=plugin_name)
            if not skill:
                continue
            if skill.name in self._cache:
                print(f"Warning: duplicate skill name '{skill.name}' in {skill_file}")
                continue
            self._cache[skill.name] = skill

        # Load plain .md command files (legacy format)
        for md_file in sorted(self.skills_dir.glob("*.md")):
            if md_file.name == "SKILL.md":
                continue
            skill = self.load_skill(md_file, source=source, plugin_name=plugin_name)
            if not skill:
                continue
            if skill.name in self._cache:
                continue
            self._cache[skill.name] = skill

        self._discovered = True
        return list(self._cache.values())

    def get_skill(self, name: str) -> Optional[Skill]:
        self._ensure_discovered()
        return self._cache.get(name)

    def list_skills(self) -> list[str]:
        self._ensure_discovered()
        return sorted(self._cache.keys())

    def get_skills_metadata_prompt(self) -> str:
        self._ensure_discovered()
        return build_skills_metadata_prompt(self._cache)

    def _resolve_paths(self, content: str, skill_dir: Path) -> str:
        def replace_link(m: re.Match[str]) -> str:
            label = m.group(1)
            target = m.group(2).strip()
            if not target:
                return m.group(0)
            if target.startswith(("#", "/", "http://", "https://", "mailto:")):
                return m.group(0)
            if re.match(r"^[a-zA-Z]+:", target):
                return m.group(0)
            abs_path = (skill_dir / target).resolve()
            return f"[{label}]({abs_path})"

        return self.LINK_RE.sub(replace_link, content)

    def _ensure_discovered(self) -> None:
        if not self._discovered:
            self.discover_skills()


def _project_skill_dirs(cwd: Path) -> list[Path]:
    dirs: list[Path] = []
    current = cwd.resolve()
    home = Path.home().resolve()
    while True:
        candidate = current / ".koder" / "skills"
        if candidate.is_dir():
            dirs.append(candidate)
        if current == home or current.parent == current:
            break
        if (current / ".git").exists():
            break
        current = current.parent

    nested = sorted(
        {path.resolve() for path in cwd.resolve().rglob(".koder/skills") if path.is_dir()},
        key=lambda path: (len(path.parts), str(path)),
    )
    for path in nested:
        if path not in dirs:
            dirs.append(path)

    # Dynamically discover new skill directories from file paths
    try:
        known_dirs = {str(d) for d in dirs}
        discovered = discover_skills_for_paths([str(cwd)], known_dirs)
        for new_dir in discovered:
            if new_dir not in dirs:
                dirs.append(new_dir)
    except Exception:
        pass  # Don't fail if discovery fails

    return dirs


def _plugin_skill_dirs(plugin_root: Path) -> list[tuple[Path, str]]:
    """Return (dir, plugin_name) pairs for plugin skills and commands.

    Both ``skills/`` (SKILL.md inside subdirs) and ``commands/``
    (plain .md files — legacy format) are scanned.
    """
    from koder_agent.harness.plugins.state import PluginStateStore

    dirs: list[tuple[Path, str]] = []
    if not plugin_root.exists():
        return dirs
    state_store = PluginStateStore(plugin_root / "state.json")
    for plugin_dir in sorted(path for path in plugin_root.iterdir() if path.is_dir()):
        plugin_name = _load_plugin_name(plugin_dir)
        if not plugin_name:
            continue
        if not state_store.is_enabled(plugin_name):
            continue
        skills_dir = plugin_dir / "skills"
        if skills_dir.is_dir():
            dirs.append((skills_dir, plugin_name))
        commands_dir = plugin_dir / "commands"
        if commands_dir.is_dir():
            dirs.append((commands_dir, plugin_name))
    return dirs


def _additional_skill_dirs(additional_dirs: list[Path]) -> list[Path]:
    dirs: list[Path] = []
    for directory in additional_dirs:
        candidate = directory / ".koder" / "skills"
        if candidate.is_dir():
            dirs.append(candidate.resolve())
    return dirs


def _additional_dirs_from_env() -> list[Path]:
    raw = os.environ.get(SKILL_ADDITIONAL_DIRS_ENV, "").strip()
    if not raw:
        return []
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()]


def _get_skill_char_budget(context_window_tokens: int | None = None) -> int:
    raw = os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET")
    if raw and raw.isdigit():
        return int(raw)
    if context_window_tokens:
        return int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT)
    return DEFAULT_CHAR_BUDGET


def build_skills_metadata_prompt(
    skills: dict[str, Skill],
    *,
    context_window_tokens: int | None = None,
) -> str:
    visible = [
        skill for skill in skills.values() if not skill.disable_model_invocation and not skill.paths
    ]
    if not visible:
        return "No skills are currently available."

    budget = _get_skill_char_budget(context_window_tokens)
    lines = ["Available skills:", ""]
    used = len("Available skills:\n\n")

    for skill in sorted(visible, key=lambda s: s.name.lower()):
        desc = (skill.description or "").strip()
        if len(desc) > MAX_LISTING_DESC_CHARS:
            desc = desc[: MAX_LISTING_DESC_CHARS - 1] + "…"
        prefix = f"- {skill.name}: "
        entry = prefix + desc
        if used + len(entry) + 1 > budget:
            remaining = budget - used - len(prefix) - 1
            if remaining < 1:
                if len(lines) == 2:
                    lines.append(prefix.rstrip())
                break
            if remaining < 20 and len(lines) > 2:
                break
            trimmed = desc[: max(0, remaining - 1)] + "…"
            entry = prefix + trimmed
        lines.append(entry)
        used += len(entry) + 1
    return "\n".join(lines)


def discover_merged_skills(
    *,
    cwd: str | Path | None = None,
    user_dir: str | Path | None = None,
    project_dir: str | Path | None = None,
    plugin_root: str | Path | None = None,
    additional_dirs: list[str | Path] | None = None,
) -> dict[str, Skill]:
    current_cwd = Path(cwd or Path.cwd()).resolve()
    resolved_user_dir = (
        _expand_path(user_dir) if user_dir is not None else (harness_home_dir() / "skills")
    )
    resolved_project_dir = _expand_path(project_dir) if project_dir is not None else None
    resolved_plugin_root = (
        _expand_path(plugin_root) if plugin_root is not None else (harness_home_dir() / "plugins")
    )
    resolved_additional_dirs = (
        [Path(path).expanduser().resolve() for path in additional_dirs]
        if additional_dirs is not None
        else _additional_dirs_from_env()
    )

    merged: dict[str, Skill] = {}

    for name, skill in get_bundled_skills().items():
        merged[name] = skill

    if resolved_user_dir.exists():
        for skill in SkillLoader(resolved_user_dir).discover_skills(source="user"):
            merged[skill.name] = skill

    if resolved_project_dir is not None and resolved_project_dir.exists():
        for skill in SkillLoader(resolved_project_dir).discover_skills(source="project"):
            merged[skill.name] = skill

    for project_dir in _project_skill_dirs(current_cwd):
        for skill in SkillLoader(project_dir).discover_skills(source="project"):
            merged[skill.name] = skill

    for extra_dir in _additional_skill_dirs(resolved_additional_dirs):
        for skill in SkillLoader(extra_dir).discover_skills(source="additional"):
            merged[skill.name] = skill

    for skills_dir, plugin_name in _plugin_skill_dirs(resolved_plugin_root):
        for skill in SkillLoader(skills_dir).discover_skills(
            source="plugin", plugin_name=plugin_name
        ):
            merged[skill.name] = skill

    return merged


class SkillModel(BaseModel):
    skill_name: str


_merged_skills: Optional[dict[str, Skill]] = None
_merged_skills_key: Optional[tuple[str, str, str, tuple[str, ...]]] = None


def _get_merged_skills() -> dict[str, Skill]:
    global _merged_skills, _merged_skills_key

    config = get_config()
    cwd = str(Path.cwd().resolve())
    user_dir = str(_expand_path(config.skills.user_skills_dir))
    project_dir = str(_expand_path(config.skills.project_skills_dir))
    plugin_root = str((harness_home_dir() / "plugins").resolve())
    additional = tuple(str(path) for path in _additional_dirs_from_env())
    cache_key = (cwd, user_dir, project_dir, plugin_root, additional)

    if _merged_skills is None or _merged_skills_key != cache_key:
        _merged_skills = discover_merged_skills(
            cwd=cwd,
            user_dir=user_dir,
            project_dir=project_dir,
            plugin_root=plugin_root,
            additional_dirs=list(additional),
        )
        _merged_skills_key = cache_key

    return _merged_skills


@function_tool
def get_skill(skill_name: str) -> str:
    from .skill_context import add_skill_restrictions, clear_restrictions

    if not skill_name:
        return "Invalid skill name: skill_name cannot be empty"

    skills = _get_merged_skills()
    skill = skills.get(skill_name)

    if skill:
        if skill.allowed_tools:
            add_skill_restrictions(skill)
        else:
            clear_restrictions()
        return skill.to_prompt()

    available = sorted(skills.keys())
    if not available:
        return f"Skill '{skill_name}' not found. No skills are currently available."
    return f"Skill '{skill_name}' not found. Available skills: {', '.join(available)}"
