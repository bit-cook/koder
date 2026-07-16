"""Parsing helpers for markdown memory files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ParsedMemoryFile:
    """Structured memory file contents."""

    memory_type: str | None
    description: str | None
    metadata: dict
    body: str


def parse_memory_file(content: str) -> ParsedMemoryFile:
    """Parse optional YAML frontmatter from a memory markdown file."""
    if content.startswith("---\n"):
        closing = content.find("\n---\n", 4)
        if closing != -1:
            frontmatter_text = content[4:closing]
            body = content[closing + 5 :].strip()
            metadata = yaml.safe_load(frontmatter_text) or {}
            return ParsedMemoryFile(
                memory_type=metadata.get("type"),
                description=metadata.get("description"),
                metadata=metadata,
                body=body,
            )
    return ParsedMemoryFile(memory_type=None, description=None, metadata={}, body=content.strip())


def save_memory_file(
    path: str | Path,
    *,
    memory_type: str,
    description: str,
    body: str,
) -> Path:
    """Persist a markdown memory file with YAML frontmatter."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_memory_file(
        memory_type=memory_type,
        description=description,
        body=body,
    )
    target.write_text(content, encoding="utf-8")
    return target


def render_memory_file(
    *,
    memory_type: str,
    description: str,
    body: str,
    metadata: dict | None = None,
) -> str:
    """Render a markdown memory file without choosing persistence semantics."""

    frontmatter_data = {"type": memory_type, "description": description}
    if metadata:
        frontmatter_data.update(metadata)
    frontmatter = yaml.safe_dump(
        frontmatter_data,
        sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n{body.strip()}\n"
