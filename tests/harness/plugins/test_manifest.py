"""Tests for plugin manifest schema, discovery, and parsing."""

import json
import sys
import types
from pathlib import Path

import pytest

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.plugins.manifest import find_manifest, parse_manifest  # noqa: E402


def _write_manifest(plugin_dir: Path, data: dict, *, use_koder_plugin: bool = False):
    """Helper to write plugin.json to a plugin directory."""
    if use_koder_plugin:
        manifest_dir = plugin_dir / ".koder-plugin"
    else:
        manifest_dir = plugin_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")


def test_find_manifest_root_plugin_json(tmp_path):
    """find_manifest locates plugin.json at plugin root."""
    plugin_dir = tmp_path / "my-plugin"
    _write_manifest(plugin_dir, {"name": "my-plugin", "version": "1.0.0"})
    result = find_manifest(plugin_dir)
    assert result is not None
    assert result.name == "plugin.json"
    assert result.parent == plugin_dir


def test_find_manifest_koder_plugin_dir(tmp_path):
    """find_manifest locates .koder-plugin/plugin.json with priority."""
    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()
    # Both locations exist; .koder-plugin should win.
    _write_manifest(plugin_dir, {"name": "root-name"})
    _write_manifest(plugin_dir, {"name": "koder-name"}, use_koder_plugin=True)
    result = find_manifest(plugin_dir)
    assert result is not None
    assert ".koder-plugin" in str(result)


def test_find_manifest_returns_none_when_missing(tmp_path):
    """find_manifest returns None when no plugin.json exists."""
    plugin_dir = tmp_path / "empty-dir"
    plugin_dir.mkdir()
    assert find_manifest(plugin_dir) is None


def test_parse_manifest_minimal(tmp_path):
    """parse_manifest succeeds with only name field."""
    plugin_dir = tmp_path / "minimal-plugin"
    _write_manifest(plugin_dir, {"name": "minimal-plugin"})
    manifest, errors, warnings = parse_manifest(plugin_dir)
    assert manifest is not None
    assert errors == []
    assert manifest.name == "minimal-plugin"
    assert manifest.version == "0.0.0"  # default


def test_parse_manifest_full_metadata(tmp_path):
    """parse_manifest parses all optional metadata fields."""
    plugin_dir = tmp_path / "full-plugin"
    _write_manifest(
        plugin_dir,
        {
            "name": "full-plugin",
            "version": "2.1.0",
            "description": "A full plugin",
            "author": {"name": "Test Author"},
            "homepage": "https://example.com",
            "repository": "https://github.com/test/full-plugin",
            "license": "MIT",
            "keywords": ["test", "full"],
            "dependencies": ["dep-a", "dep-b"],
        },
    )
    manifest, errors, warnings = parse_manifest(plugin_dir)
    assert errors == []
    assert manifest is not None
    assert manifest.name == "full-plugin"
    assert manifest.version == "2.1.0"
    assert manifest.description == "A full plugin"
    assert manifest.author == "Test Author"
    assert manifest.homepage == "https://example.com"
    assert manifest.license == "MIT"
    assert manifest.keywords == ("test", "full")
    assert manifest.dependencies == ("dep-a", "dep-b")


def test_parse_manifest_component_paths(tmp_path):
    """parse_manifest parses component path fields."""
    plugin_dir = tmp_path / "components-plugin"
    _write_manifest(
        plugin_dir,
        {
            "name": "components-plugin",
            "skills": "skills/",
            "agents": "agents/",
            "hooks": "hooks/hooks.json",
            "mcpServers": ".mcp.json",
        },
    )
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert errors == []
    assert manifest is not None
    assert manifest.skills == "skills"
    assert manifest.agents == "agents"
    assert manifest.hooks == "hooks/hooks.json"
    assert manifest.mcp_servers == ".mcp.json"


def test_parse_manifest_rejects_missing_name(tmp_path):
    """parse_manifest errors when name field is missing."""
    plugin_dir = tmp_path / "no-name"
    _write_manifest(plugin_dir, {"version": "1.0.0"})
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert manifest is None
    assert any("name" in e for e in errors)


@pytest.mark.parametrize("name", [None, 123, ["demo"]])
def test_parse_manifest_rejects_non_string_names(tmp_path, name):
    plugin_dir = tmp_path / "non-string-name"
    _write_manifest(plugin_dir, {"name": name})

    manifest, errors, _ = parse_manifest(plugin_dir)

    assert manifest is None
    assert any("must be a non-empty string" in error for error in errors)


def test_parse_manifest_rejects_invalid_json(tmp_path):
    """parse_manifest errors for malformed JSON."""
    plugin_dir = tmp_path / "bad-json"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{bad", encoding="utf-8")
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert manifest is None
    assert any("Invalid JSON" in e for e in errors)


def test_parse_manifest_rejects_path_traversal(tmp_path):
    """parse_manifest blocks .. in component paths."""
    plugin_dir = tmp_path / "traversal-plugin"
    _write_manifest(
        plugin_dir,
        {"name": "traversal-plugin", "skills": "../outside/skills"},
    )
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert manifest is None
    assert any("Path traversal" in e for e in errors)


@pytest.mark.parametrize(
    "component_path",
    ["/tmp/outside", r"C:\outside", r"nested\outside", "skills//nested", "./skills"],
)
def test_parse_manifest_rejects_nonportable_component_paths(tmp_path, component_path):
    plugin_dir = tmp_path / "unsafe-component"
    _write_manifest(plugin_dir, {"name": "unsafe-component", "skills": component_path})

    manifest, errors, _ = parse_manifest(plugin_dir)

    assert manifest is None
    assert errors


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        r"..\outside",
        "/tmp/absolute-plugin",
        r"C:\absolute-plugin",
        "nested/plugin",
        r"nested\plugin",
        ".",
        "..",
        ".hidden-plugin",
        "hidden-plugin.",
        "-leading-separator",
        "trailing-separator_",
        "CON",
        "nul",
    ],
)
def test_parse_manifest_rejects_unsafe_plugin_names(tmp_path, name):
    plugin_dir = tmp_path / "unsafe-name"
    _write_manifest(plugin_dir, {"name": name})

    manifest, errors, _ = parse_manifest(plugin_dir)

    assert manifest is None
    assert any("Invalid plugin name" in error for error in errors)


def test_parse_manifest_rejects_noncanonical_case_with_migration_message(tmp_path):
    """Existing mixed-case identities must be renamed, never silently aliased."""
    plugin_dir = tmp_path / "BadName"
    _write_manifest(plugin_dir, {"name": "BadName"})
    manifest, errors, warnings = parse_manifest(plugin_dir)
    assert manifest is None
    assert warnings == []
    assert any("lowercase canonical spelling" in error for error in errors)


def test_parse_manifest_preserves_compatible_long_and_dotted_names(tmp_path):
    """Portable names accepted before traversal hardening remain compatible."""
    plugin_dir = tmp_path / "long-name"
    long_name = f"acme.{('a' * 95)}"
    _write_manifest(plugin_dir, {"name": long_name})
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert errors == []
    assert manifest is not None
    assert manifest.name == long_name


def test_parse_manifest_rejects_names_over_portable_filename_limit(tmp_path):
    plugin_dir = tmp_path / "too-long"
    _write_manifest(plugin_dir, {"name": "a" * 256})

    manifest, errors, _ = parse_manifest(plugin_dir)

    assert manifest is None
    assert any("255 characters" in error for error in errors)


def test_parse_manifest_from_koder_plugin_dir(tmp_path):
    """parse_manifest works with .koder-plugin/plugin.json location."""
    plugin_dir = tmp_path / "koder-style"
    _write_manifest(plugin_dir, {"name": "koder-style", "version": "1.0.0"}, use_koder_plugin=True)
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert errors == []
    assert manifest is not None
    assert manifest.name == "koder-style"


def test_parse_manifest_author_string(tmp_path):
    """parse_manifest handles author as plain string."""
    plugin_dir = tmp_path / "author-str"
    _write_manifest(plugin_dir, {"name": "author-str", "author": "Jane Doe"})
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert errors == []
    assert manifest is not None
    assert manifest.author == "Jane Doe"


def test_parse_manifest_no_manifest_file(tmp_path):
    """parse_manifest returns error when directory has no plugin.json."""
    plugin_dir = tmp_path / "empty"
    plugin_dir.mkdir()
    manifest, errors, _ = parse_manifest(plugin_dir)
    assert manifest is None
    assert any("No plugin.json found" in e for e in errors)
