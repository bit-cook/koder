"""Adversarial tests for descriptor-pinned plugin component access."""

from pathlib import Path

from koder_agent.harness.plugins import path_safety
from koder_agent.harness.plugins.path_safety import (
    open_plugin_component,
    snapshot_plugin_tree,
)


def test_open_file_component_is_not_redirected_by_path_swap(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    config = plugin / ".mcp.json"
    config.write_text("original", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replacement", encoding="utf-8")

    with open_plugin_component(
        plugin,
        ".mcp.json",
        default=".mcp.json",
        field_name="mcpServers",
        expect="file",
    ) as pinned:
        config.unlink()
        config.symlink_to(replacement)
        assert pinned is not None
        assert pinned.read_text(encoding="utf-8") == "original"


def test_file_fallback_snapshot_is_not_redirected_by_path_swap(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    config = plugin / ".mcp.json"
    config.write_text("original", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replacement", encoding="utf-8")
    monkeypatch.setattr(path_safety, "_descriptor_path", lambda _descriptor: None)

    with open_plugin_component(
        plugin,
        ".mcp.json",
        default=".mcp.json",
        field_name="mcpServers",
        expect="file",
    ) as snapshot:
        assert snapshot is not None
        config.unlink()
        config.symlink_to(replacement)
        assert snapshot.read_text(encoding="utf-8") == "original"
        snapshot_path = Path(snapshot)

    assert snapshot_path.exists() is False


def test_open_directory_component_uses_isolated_snapshot(tmp_path):
    plugin = tmp_path / "plugin"
    skills = plugin / "skills"
    skills.mkdir(parents=True)
    source = skills / "SKILL.md"
    source.write_text("original", encoding="utf-8")

    with open_plugin_component(
        plugin,
        "skills",
        default="skills",
        field_name="skills",
        expect="directory",
    ) as snapshot:
        source.write_text("replacement", encoding="utf-8")
        assert snapshot is not None
        assert (Path(snapshot) / "SKILL.md").read_text(encoding="utf-8") == "original"

    assert Path(snapshot).exists() is False


def test_directory_snapshots_are_removed_after_each_context(tmp_path):
    plugin = tmp_path / "plugin"
    skills = plugin / "skills"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("content", encoding="utf-8")
    snapshots: list[Path] = []

    for _ in range(3):
        with open_plugin_component(
            plugin,
            "skills",
            default="skills",
            field_name="skills",
            expect="directory",
        ) as snapshot:
            assert snapshot is not None
            snapshot_path = Path(snapshot)
            snapshots.append(snapshot_path)
            assert snapshot_path.is_dir()
        assert snapshot_path.exists() is False

    assert all(snapshot.exists() is False for snapshot in snapshots)


def test_plugin_tree_snapshot_is_removed_when_context_exits(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text('{"name": "demo"}', encoding="utf-8")

    with snapshot_plugin_tree(plugin) as snapshot:
        assert (snapshot / "plugin.json").is_file()
        snapshot_path = Path(snapshot)

    assert snapshot_path.exists() is False
