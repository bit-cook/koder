"""Dynamic skill discovery from file paths during sessions."""

from fnmatch import fnmatch
from pathlib import Path


def discover_skills_for_paths(paths: list[str], known_dirs: set[str]) -> list[Path]:
    """
    Scan file paths for new skill directories not already in known_dirs.

    Walks up parent directories from each file path looking for .koder/skills/
    directories. Returns only directories not already tracked in known_dirs.

    Args:
        paths: List of file paths to scan
        known_dirs: Set of already-known skill directory paths (as strings)

    Returns:
        List of newly discovered Path objects to .koder/skills/ directories
    """
    if not paths:
        return []

    discovered = set()
    # Normalize known_dirs to resolved paths for comparison
    known_resolved = {str(Path(d).resolve()) for d in known_dirs}

    for file_path in paths:
        path = Path(file_path).resolve()

        # Walk up parent directories
        for parent in [path.parent] + list(path.parents):
            skills_dir = parent / ".koder" / "skills"
            if skills_dir.exists() and skills_dir.is_dir():
                skills_dir_resolved = skills_dir.resolve()
                skills_dir_str = str(skills_dir_resolved)
                if skills_dir_str not in known_resolved and skills_dir_resolved not in discovered:
                    discovered.add(skills_dir_resolved)

    return list(discovered)


def activate_conditional_skills(skills: dict[str, object], file_path: str) -> list[str]:
    """
    Check skills with paths frontmatter field and return names of activated skills.

    Examines each skill's metadata for a 'paths' field containing fnmatch patterns.
    Returns skill names where at least one pattern matches the given file_path.

    Args:
        skills: Dictionary mapping skill names to skill objects with metadata attribute
        file_path: File path to check against skill patterns

    Returns:
        List of skill names that should be activated for this file path
    """
    activated = []

    for skill_name, skill in skills.items():
        # Real Skill objects store patterns in the dedicated ``paths`` field
        # (stripped from metadata at load time). Prefer it, then fall back to
        # ``metadata["paths"]`` for dict/Mock-based inputs. Only accept an actual
        # list/tuple of patterns so auto-created Mock attributes are ignored.
        path_patterns = getattr(skill, "paths", None)
        if not isinstance(path_patterns, (list, tuple)) or not path_patterns:
            metadata = getattr(skill, "metadata", None) or {}
            path_patterns = metadata.get("paths", []) if isinstance(metadata, dict) else []

        if not isinstance(path_patterns, (list, tuple)) or not path_patterns:
            continue

        for pattern in path_patterns:
            if fnmatch(file_path, pattern):
                activated.append(skill_name)
                break

    return activated
