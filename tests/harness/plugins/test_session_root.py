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

from koder_agent.harness.plugins import session_root as session_root_module
from koder_agent.harness.plugins.manifest import PluginManifest
from koder_agent.harness.plugins.session_root import build_session_plugin_root
from koder_agent.harness.plugins.state import PluginState


def _write_plugin(plugin_dir: Path, name: object) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")


def _use_test_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(session_root_module, "harness_home_dir", lambda: home)
    return home


@pytest.mark.parametrize("name", ["../outside", None])
def test_session_root_rejects_parser_bypass_without_escape(tmp_path, monkeypatch, name):
    home = _use_test_home(monkeypatch, tmp_path)
    base_root = tmp_path / "base"
    base_root.mkdir()
    plugin_dir = tmp_path / "source"
    _write_plugin(plugin_dir, "placeholder")
    outside = home / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("intact", encoding="utf-8")
    manifest = PluginManifest(name=name, plugin_dir=plugin_dir)  # type: ignore[arg-type]
    monkeypatch.setattr(
        session_root_module,
        "parse_manifest",
        lambda _plugin_dir: (manifest, [], []),
    )

    with pytest.raises(ValueError, match="Invalid plugin name"):
        build_session_plugin_root("session", [plugin_dir], base_root=base_root)

    assert sentinel.read_text(encoding="utf-8") == "intact"
    assert not (home / "session-plugins" / "session").exists()


def test_session_root_rejects_installed_plugin_parser_bypass(tmp_path, monkeypatch):
    home = _use_test_home(monkeypatch, tmp_path)
    base_root = tmp_path / "base"
    base_root.mkdir()
    outside = home / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("intact", encoding="utf-8")
    manifest = PluginManifest(name="../outside", plugin_dir=base_root)
    monkeypatch.setattr(
        session_root_module.PluginLifecycleService,
        "installed_plugins",
        lambda _self: [(manifest, PluginState())],
    )

    with pytest.raises(ValueError, match="Invalid plugin name"):
        build_session_plugin_root("session", [], base_root=base_root)

    assert sentinel.read_text(encoding="utf-8") == "intact"
    assert not (home / "session-plugins" / "session").exists()


def test_session_root_replaces_existing_symlink_entry_without_following_it(tmp_path, monkeypatch):
    home = _use_test_home(monkeypatch, tmp_path)
    base_root = tmp_path / "base"
    base_root.mkdir()
    plugin_dir = tmp_path / "source"
    _write_plugin(plugin_dir, "demo")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("intact", encoding="utf-8")
    session_parent = home / "session-plugins"
    session_parent.mkdir()
    (session_parent / "session").symlink_to(outside, target_is_directory=True)

    result = build_session_plugin_root("session", [plugin_dir], base_root=base_root)

    assert result == (session_parent / "session").resolve()
    assert not (session_parent / "session").is_symlink()
    assert (result / "demo" / "plugin.json").is_file()
    assert sentinel.read_text(encoding="utf-8") == "intact"


def test_session_root_staging_failure_preserves_previous_overlay(tmp_path, monkeypatch):
    home = _use_test_home(monkeypatch, tmp_path)
    base_root = tmp_path / "base"
    base_root.mkdir()
    plugin_dir = tmp_path / "source"
    _write_plugin(plugin_dir, "demo")
    existing = home / "session-plugins" / "session"
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("old overlay", encoding="utf-8")
    monkeypatch.setattr(
        session_root_module.PluginLifecycleService,
        "install_from_manifest",
        lambda *_args, **_kwargs: types.SimpleNamespace(success=False, message="staging failed"),
    )

    with pytest.raises(ValueError, match="staging failed"):
        build_session_plugin_root("session", [plugin_dir], base_root=base_root)

    assert sentinel.read_text(encoding="utf-8") == "old overlay"


def test_dot_session_identifier_cannot_escape_session_parent(tmp_path, monkeypatch):
    home = _use_test_home(monkeypatch, tmp_path)
    base_root = tmp_path / "base"
    base_root.mkdir()

    result = build_session_plugin_root("..", [], base_root=base_root)

    assert result == (home / "session-plugins" / "session").resolve()


def test_session_root_rejects_symlinked_parent(tmp_path, monkeypatch):
    home = _use_test_home(monkeypatch, tmp_path)
    outside = tmp_path / "outside-session-parent"
    outside.mkdir()
    (home / "session-plugins").symlink_to(outside, target_is_directory=True)
    base_root = tmp_path / "base"
    base_root.mkdir()

    with pytest.raises(ValueError, match="symlink"):
        build_session_plugin_root("session", [], base_root=base_root)

    assert list(outside.iterdir()) == []
