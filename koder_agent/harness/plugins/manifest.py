"""Plugin manifest schema, discovery, and parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .name_validation import validate_plugin_name_format
from .path_safety import (
    PluginPathError,
    open_plugin_component,
    resolve_plugin_component,
    validate_component_path,
)


@dataclass(frozen=True)
class PluginManifest:
    """Parsed plugin.json manifest."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    repository: str = ""
    license: str = ""
    keywords: tuple[str, ...] = ()

    # Component paths (relative to plugin root)
    commands: str | None = None
    agents: str | None = None
    skills: str | None = None
    hooks: str | None = None
    mcp_servers: str | None = None
    lsp_servers: str | None = None

    # Dependencies
    dependencies: tuple[str, ...] = ()

    # Trust
    requires_trust_ack: bool = False

    # The directory containing the manifest
    plugin_dir: Path = field(default_factory=lambda: Path("."))


def find_manifest(plugin_dir: Path) -> Path | None:
    """Find plugin.json in a plugin directory.

    Search order:
    1. <plugin_dir>/.koder-plugin/plugin.json
    2. <plugin_dir>/plugin.json
    """
    try:
        koder_plugin = resolve_plugin_component(
            plugin_dir,
            ".koder-plugin/plugin.json",
            default=".koder-plugin/plugin.json",
            field_name="manifest",
            expect="file",
        )
        if koder_plugin is not None:
            return koder_plugin
        return resolve_plugin_component(
            plugin_dir,
            "plugin.json",
            default="plugin.json",
            field_name="manifest",
            expect="file",
        )
    except PluginPathError:
        return None


def parse_manifest(
    plugin_dir: Path,
) -> tuple[PluginManifest | None, list[str], list[str]]:
    """Parse and validate a plugin manifest.

    Returns (manifest, errors, warnings).
    If errors is non-empty, manifest is None.
    """
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path: Path | None = None
    raw: object | None = None
    try:
        for relative in (".koder-plugin/plugin.json", "plugin.json"):
            with open_plugin_component(
                plugin_dir,
                relative,
                default=relative,
                field_name="manifest",
                expect="file",
            ) as opened_manifest:
                if opened_manifest is None:
                    continue
                manifest_path = plugin_dir.joinpath(*relative.split("/"))
                raw = json.loads(opened_manifest.read_text(encoding="utf-8"))
                break
        if manifest_path is None:
            errors.append(
                "No plugin.json found (checked .koder-plugin/plugin.json and plugin.json)"
            )
            return None, errors, warnings
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON in {manifest_path}: {exc}")
        return None, errors, warnings
    except (OSError, PluginPathError) as exc:
        errors.append(f"Cannot read {manifest_path}: {exc}")
        return None, errors, warnings

    if not isinstance(raw, dict):
        errors.append("plugin.json must be a JSON object")
        return None, errors, warnings

    # Required: name
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("'name' field is required and must be a non-empty string")
        return None, errors, warnings

    # Name validation
    is_valid_name, name_error = validate_plugin_name_format(name)
    if not is_valid_name:
        errors.append(f"Invalid plugin name '{name}': {name_error}")

    # Version
    version = str(raw.get("version", "0.0.0"))

    # Optional metadata
    description = str(raw.get("description", ""))
    author_val = raw.get("author", "")
    if isinstance(author_val, dict):
        author = str(author_val.get("name", ""))
    else:
        author = str(author_val)
    homepage = str(raw.get("homepage", ""))
    repository = str(raw.get("repository", ""))
    license_val = str(raw.get("license", ""))
    keywords_raw = raw.get("keywords", [])
    keywords = tuple(str(k) for k in keywords_raw) if isinstance(keywords_raw, list) else ()

    # Component paths are portable relative paths. Runtime discovery resolves
    # them again beneath a symlink-free plugin root before use.
    def _validate_path(field_name: str) -> str | None:
        val = raw.get(field_name)
        if val is None:
            return None
        normalized, error = validate_component_path(val, field_name=field_name)
        if normalized is None:
            errors.append(error)
            return None
        return normalized

    commands = _validate_path("commands")
    agents = _validate_path("agents")
    skills = _validate_path("skills")
    hooks = _validate_path("hooks")
    mcp_servers = _validate_path("mcpServers")
    lsp_servers = _validate_path("lspServers")

    # Dependencies
    deps_raw = raw.get("dependencies", [])
    if isinstance(deps_raw, list):
        dependencies = tuple(str(d) for d in deps_raw)
    else:
        dependencies = ()

    # Trust
    requires_trust = bool(raw.get("requires_trust_ack", False))

    if errors:
        return None, errors, warnings

    manifest = PluginManifest(
        name=name,
        version=version,
        description=description,
        author=author,
        homepage=homepage,
        repository=repository,
        license=license_val,
        keywords=keywords,
        commands=commands,
        agents=agents,
        skills=skills,
        hooks=hooks,
        mcp_servers=mcp_servers,
        lsp_servers=lsp_servers,
        dependencies=dependencies,
        requires_trust_ack=requires_trust,
        plugin_dir=plugin_dir,
    )
    return manifest, errors, warnings
