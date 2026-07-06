"""Output-style persona loader.

Output styles are markdown files (``*.md``) with optional YAML frontmatter
(``name`` / ``description``). The *body* of the file becomes part of the agent
system prompt, letting users define a persona (e.g. a pirate, a terse reviewer)
rather than only changing the terminal theme.

Discovery mirrors the skills/agents conventions:

- Project styles live in ``<cwd>/.koder/output-styles/*.md``
- User styles live in ``~/.koder/output-styles/*.md``

Project styles override user styles with the same name. The active style is
persisted in ``settings.json`` under ``outputStyle.style`` (a separate concern
from ``outputStyle.theme``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from koder_agent.harness.paths import harness_home_dir, harness_project_dir

FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)$", re.DOTALL)


@dataclass
class OutputStyle:
    """A loaded output-style persona."""

    name: str
    description: str
    body: str
    source: str  # "project" or "user"
    path: Path


def user_output_styles_dir() -> Path:
    return harness_home_dir() / "output-styles"


def project_output_styles_dir(cwd: str | Path) -> Path:
    return harness_project_dir(cwd) / "output-styles"


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter dict, body)."""
    text = raw.lstrip("﻿")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    loaded = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, match.group("body").strip()


def parse_output_style_file(path: Path, *, source: str) -> Optional[OutputStyle]:
    """Parse a single output-style markdown file.

    Returns ``None`` when the file cannot be read. The ``name`` falls back to the
    file stem when no frontmatter ``name`` is provided.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    frontmatter, body = _parse_frontmatter(raw)
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        name = path.stem
    name = name.strip()
    description = frontmatter.get("description")
    if not isinstance(description, str):
        description = ""
    description = description.strip()
    return OutputStyle(
        name=name,
        description=description,
        body=body.strip(),
        source=source,
        path=path,
    )


def _discover_in_dir(directory: Path, *, source: str) -> list[OutputStyle]:
    if not directory.exists() or not directory.is_dir():
        return []
    styles: list[OutputStyle] = []
    for file_path in sorted(directory.glob("*.md")):
        if not file_path.is_file():
            continue
        style = parse_output_style_file(file_path, source=source)
        if style is not None:
            styles.append(style)
    return styles


def discover_output_styles(cwd: str | Path | None = None) -> dict[str, OutputStyle]:
    """Discover all output styles.

    Project styles override user styles that share the same (case-insensitive)
    name. Returns a mapping keyed by the lowercase style name.
    """
    cwd = Path.cwd() if cwd is None else Path(cwd)
    merged: dict[str, OutputStyle] = {}
    # User styles first so project styles override on name collision.
    for style in _discover_in_dir(user_output_styles_dir(), source="user"):
        merged[style.name.lower()] = style
    for style in _discover_in_dir(project_output_styles_dir(cwd), source="project"):
        merged[style.name.lower()] = style
    return merged


def find_output_style(name: str, cwd: str | Path | None = None) -> Optional[OutputStyle]:
    """Look up a single style by name (case-insensitive)."""
    if not name:
        return None
    return discover_output_styles(cwd).get(name.strip().lower())


def _output_style_settings_path() -> Path:
    return harness_home_dir() / "settings.json"


def _read_settings() -> dict[str, Any]:
    settings_path = _output_style_settings_path()
    try:
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_active_output_style_name() -> Optional[str]:
    """Return the persisted active style name, or ``None`` if unset."""
    loaded = _read_settings()
    output_style = loaded.get("outputStyle")
    if not isinstance(output_style, dict):
        return None
    style = output_style.get("style")
    if isinstance(style, str) and style.strip():
        return style.strip()
    return None


def save_active_output_style_name(name: Optional[str]) -> Path:
    """Persist (or clear) the active style name under ``outputStyle.style``.

    Passing ``None`` clears the active style while preserving ``outputStyle``'s
    other keys (e.g. ``theme``).
    """
    settings_path = _output_style_settings_path()
    loaded = _read_settings()
    output_style = loaded.get("outputStyle")
    if not isinstance(output_style, dict):
        output_style = {}
    if name is None:
        output_style.pop("style", None)
    else:
        output_style["style"] = name
    loaded["outputStyle"] = output_style
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return settings_path


def load_active_output_style_body(cwd: str | Path | None = None) -> Optional[str]:
    """Return the body of the active persona style for prompt injection.

    Returns ``None`` when no style is active or the active style no longer
    resolves to a discoverable file (or has an empty body).
    """
    name = load_active_output_style_name()
    if not name:
        return None
    style = find_output_style(name, cwd)
    if style is None or not style.body.strip():
        return None
    return style.body.strip()
