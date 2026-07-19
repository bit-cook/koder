import json
import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.plugins import lifecycle as lifecycle_module
from koder_agent.harness.plugins.lifecycle import PluginLifecycleService
from koder_agent.harness.plugins.manifest import PluginManifest


def _write_plugin(plugin_dir: Path, name: str, version: str = "1.0.0") -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )


def test_plugin_install_rejects_invalid_json(tmp_path):
    """Invalid JSON in plugin.json is caught during manifest validation."""
    service = PluginLifecycleService.for_test(tmp_path)
    broken_dir = tmp_path / "broken_plugin"
    broken_dir.mkdir()
    (broken_dir / "plugin.json").write_text("{not-json", encoding="utf-8")
    result = service.install_from_dir(broken_dir)
    assert result.success is False
    assert "Invalid JSON" in result.message


def test_plugin_install_rolls_back_on_copy_error(tmp_path):
    """Rollback occurs if copy fails after manifest validation passes."""
    service = PluginLifecycleService.for_test(tmp_path)
    valid_dir = tmp_path / "valid-plugin"
    valid_dir.mkdir()
    (valid_dir / "plugin.json").write_text(
        '{"name": "valid-plugin", "version": "1.0.0"}', encoding="utf-8"
    )
    # Install should succeed
    result = service.install_from_dir(valid_dir)
    assert result.success is True
    assert result.plugin_name == "valid-plugin"


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_install_never_copies_content_reached_through_source_symlink(tmp_path, link_kind):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside secret", encoding="utf-8")
    if link_kind == "file":
        (source / "linked.txt").symlink_to(sentinel)
    else:
        (source / "linked-dir").symlink_to(outside, target_is_directory=True)

    result = service.install_from_dir(source)

    assert result.success is False
    assert "symlink" in result.message
    assert not (service.root / "demo").exists()
    assert sentinel.read_text(encoding="utf-8") == "outside secret"


def test_plugin_lifecycle_preserves_normal_install_upgrade_and_state_changes(tmp_path):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_dir = tmp_path / "normal-plugin-source"
    _write_plugin(plugin_dir, "normal-plugin")
    (plugin_dir / "payload.txt").write_text("v1", encoding="utf-8")

    assert service.install_from_dir(plugin_dir).success is True
    installed_dir = service.root / "normal-plugin"
    assert (installed_dir / "payload.txt").read_text(encoding="utf-8") == "v1"

    assert service.disable("normal-plugin").success is True
    assert service.is_enabled("normal-plugin") is False
    assert service.enable("normal-plugin").success is True
    assert service.is_enabled("normal-plugin") is True

    (plugin_dir / "payload.txt").write_text("v2", encoding="utf-8")
    _write_plugin(plugin_dir, "normal-plugin", version="2.0.0")
    assert service.install_from_dir(plugin_dir).success is True
    assert (installed_dir / "payload.txt").read_text(encoding="utf-8") == "v2"

    assert service.uninstall("normal-plugin").success is True
    assert installed_dir.exists() is False


def test_long_valid_plugin_identity_supports_atomic_upgrade_and_recovery(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_name = "a" * 255
    source = tmp_path / "long-plugin-source"
    _write_plugin(source, plugin_name)
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source).success is True

    (source / "payload.txt").write_text("v2", encoding="utf-8")
    real_set = service.state_store.set
    failed = False

    def fail_once(name, state):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("state write failed")
        return real_set(name, state)

    monkeypatch.setattr(service.state_store, "set", fail_once)
    result = service.install_from_dir(source)

    assert result.success is False
    assert result.rollback_performed is True
    assert (service.root / plugin_name / "payload.txt").read_text(encoding="utf-8") == "v1"
    assert not any(
        path.name.startswith((".koder-stage-", ".koder-backup-")) for path in service.root.iterdir()
    )


def test_install_rejects_unsafe_manifest_name_when_parser_is_bypassed(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_dir = tmp_path / "malicious-source"
    _write_plugin(plugin_dir, "placeholder")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    malformed_manifest = PluginManifest(name="../outside", plugin_dir=plugin_dir)
    monkeypatch.setattr(
        lifecycle_module,
        "parse_manifest",
        lambda _plugin_dir: (malformed_manifest, [], []),
    )

    result = service.install_from_dir(plugin_dir)

    assert result.success is False
    assert "Invalid plugin name" in result.message
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


@pytest.mark.parametrize("action", ["uninstall", "enable", "disable"])
@pytest.mark.parametrize(
    "plugin_name",
    ["../outside", r"..\outside", "nested/plugin", r"nested\plugin", ".", ".."],
)
def test_named_lifecycle_actions_reject_unsafe_names(tmp_path, action, plugin_name):
    service = PluginLifecycleService.for_test(tmp_path)
    root_sentinel = service.root / "sentinel.txt"
    root_sentinel.write_text("root intact", encoding="utf-8")

    result = getattr(service, action)(plugin_name)

    assert result.success is False
    assert "Invalid plugin name" in result.message
    assert root_sentinel.read_text(encoding="utf-8") == "root intact"


def test_uninstall_rejects_absolute_path_and_preserves_outside_sentinel(tmp_path):
    service = PluginLifecycleService.for_test(tmp_path)
    outside_dir = tmp_path / "absolute-outside"
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")

    result = service.uninstall(str(outside_dir))

    assert result.success is False
    assert "Invalid plugin name" in result.message
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


@pytest.mark.parametrize("action", ["install", "uninstall", "enable", "disable"])
def test_lifecycle_actions_reject_symlink_targets(tmp_path, action):
    service = PluginLifecycleService.for_test(tmp_path)
    outside_dir = tmp_path / "symlink-outside"
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    target = service.root / "linked-plugin"
    target.symlink_to(outside_dir, target_is_directory=True)

    if action == "install":
        plugin_dir = tmp_path / "linked-plugin-source"
        _write_plugin(plugin_dir, "linked-plugin")
        result = service.install_from_dir(plugin_dir)
    else:
        result = getattr(service, action)("linked-plugin")

    assert result.success is False
    assert "symlink" in result.message
    assert target.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_install_rejects_non_string_manifest_name_when_parser_is_bypassed(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_dir = tmp_path / "malformed-source"
    _write_plugin(plugin_dir, "placeholder")
    malformed_manifest = PluginManifest(name=None, plugin_dir=plugin_dir)  # type: ignore[arg-type]
    monkeypatch.setattr(
        lifecycle_module,
        "parse_manifest",
        lambda _plugin_dir: (malformed_manifest, [], []),
    )

    result = service.install_from_dir(plugin_dir)

    assert result.success is False
    assert result.plugin_name is None
    assert "must be a string" in result.message


@pytest.mark.parametrize("name", ["marketplace-cache", "state.json", "marketplaces.json"])
def test_install_rejects_reserved_infrastructure_manifest_on_parser_bypass(
    tmp_path, monkeypatch, name
):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_dir = tmp_path / "reserved-source"
    _write_plugin(plugin_dir, "placeholder")
    manifest = PluginManifest(name=name, plugin_dir=plugin_dir)
    monkeypatch.setattr(
        lifecycle_module,
        "parse_manifest",
        lambda _plugin_dir: (manifest, [], []),
    )

    result = service.install_from_dir(plugin_dir)

    assert result.success is False
    assert "infrastructure" in result.message
    assert (service.root / name).exists() is False


def test_case_alias_install_is_rejected_without_replacing_canonical_plugin(tmp_path):
    service = PluginLifecycleService.for_test(tmp_path)
    canonical_source = tmp_path / "canonical-source"
    _write_plugin(canonical_source, "demo")
    (canonical_source / "payload.txt").write_text("canonical", encoding="utf-8")
    assert service.install_from_dir(canonical_source).success is True

    alias_source = tmp_path / "alias-source"
    _write_plugin(alias_source, "Demo")
    (alias_source / "payload.txt").write_text("alias", encoding="utf-8")

    result = service.install_from_dir(alias_source)

    assert result.success is False
    assert "lowercase canonical spelling" in result.message
    assert (service.root / "demo" / "payload.txt").read_text(encoding="utf-8") == "canonical"
    assert [path.name for path in service.root.iterdir() if path.is_dir()] == ["demo"]


@pytest.mark.parametrize("action", ["uninstall", "enable", "disable"])
def test_named_actions_reject_case_aliases(tmp_path, action):
    service = PluginLifecycleService.for_test(tmp_path)
    installed = service.root / "demo"
    _write_plugin(installed, "demo")

    result = getattr(service, action)("Demo")

    assert result.success is False
    assert "lowercase canonical spelling" in result.message
    assert installed.is_dir()


def test_unambiguous_legacy_case_directory_and_state_are_migrated(tmp_path):
    root = tmp_path / "installed_plugins"
    root.mkdir()
    legacy_dir = root / "Demo"
    _write_plugin(legacy_dir, "Demo")
    (root / "state.json").write_text(
        json.dumps({"Demo": {"enabled": False, "scope": "project"}}),
        encoding="utf-8",
    )

    service = PluginLifecycleService.for_test(tmp_path)

    installed = service.installed_plugins()
    assert [(manifest.name, state.enabled, state.scope) for manifest, state in installed] == [
        ("demo", False, "project")
    ]
    assert "Demo" not in {path.name for path in service.root.iterdir()}
    assert (service.root / "demo" / "plugin.json").is_file()


def test_symlinked_root_is_rejected_before_targets_or_state_are_opened(tmp_path):
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    first_root.mkdir()
    second_root.mkdir()
    root_link = tmp_path / "plugins-link"
    root_link.symlink_to(first_root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        PluginLifecycleService(root_link)

    assert list(first_root.iterdir()) == []
    assert list(second_root.iterdir()) == []


def test_root_path_swap_cannot_redirect_lifecycle_mutations(tmp_path):
    service = PluginLifecycleService.for_test(tmp_path)
    original_root = service.root
    pinned_root = tmp_path / "pinned-root"
    outside = tmp_path / "outside-root"
    outside.mkdir()
    original_root.rename(pinned_root)
    original_root.symlink_to(outside, target_is_directory=True)
    source = tmp_path / "source"
    _write_plugin(source, "demo")

    result = service.install_from_dir(source)

    assert result.success is False
    assert "symlink" in result.message or "identity changed" in result.message
    assert list(outside.iterdir()) == []
    assert not (pinned_root / "demo").exists()


@pytest.mark.parametrize("action", ["install", "uninstall", "enable", "disable"])
def test_lifecycle_state_symlink_never_overwrites_outside_file(tmp_path, action):
    service = PluginLifecycleService.for_test(tmp_path)
    outside_state = tmp_path / "outside-state.json"
    outside_state.write_text('{"sentinel": true}', encoding="utf-8")
    (service.root / "state.json").symlink_to(outside_state)
    installed = service.root / "demo"
    _write_plugin(installed, "demo")

    if action == "install":
        source = tmp_path / "source"
        _write_plugin(source, "demo")
        result = service.install_from_dir(source)
    else:
        result = getattr(service, action)("demo")

    assert result.success is False
    assert "symlinked plugin state" in result.message
    assert outside_state.read_text(encoding="utf-8") == '{"sentinel": true}'
    assert installed.is_dir()


def test_copy_failure_leaves_existing_plugin_and_cleans_staging(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source).success is True
    (source / "payload.txt").write_text("v2", encoding="utf-8")

    def fail_copy(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(lifecycle_module, "copy_tree_without_links", fail_copy)
    result = service.install_from_dir(source)

    assert result.success is False
    assert (service.root / "demo" / "payload.txt").read_text(encoding="utf-8") == "v1"
    assert not any(path.name.startswith(".koder-stage-") for path in service.root.iterdir())


def test_atomic_replacement_failure_restores_previous_plugin(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source).success is True
    (source / "payload.txt").write_text("v2", encoding="utf-8")
    real_replace = service._paths.replace

    def fail_publish(source_name, target_name):
        if source_name.startswith(".koder-stage-") and target_name == "demo":
            raise OSError("publish failed")
        return real_replace(source_name, target_name)

    monkeypatch.setattr(service._paths, "replace", fail_publish)
    result = service.install_from_dir(source)

    assert result.success is False
    assert result.rollback_performed is True
    assert (service.root / "demo" / "payload.txt").read_text(encoding="utf-8") == "v1"


def test_state_write_failure_rolls_back_atomic_plugin_replacement(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source).success is True
    (source / "payload.txt").write_text("v2", encoding="utf-8")
    real_set = service.state_store.set
    failed = False

    def fail_once(name, state):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("state write failed")
        return real_set(name, state)

    monkeypatch.setattr(service.state_store, "set", fail_once)
    result = service.install_from_dir(source)

    assert result.success is False
    assert result.rollback_performed is True
    assert (service.root / "demo" / "payload.txt").read_text(encoding="utf-8") == "v1"


def test_uninstall_state_failure_restores_plugin_directory(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    assert service.install_from_dir(source).success is True

    def fail_remove(_name):
        raise OSError("state remove failed")

    monkeypatch.setattr(service.state_store, "remove", fail_remove)

    result = service.uninstall("demo")

    assert result.success is False
    assert result.rollback_performed is True
    assert (service.root / "demo" / "plugin.json").is_file()


def test_rollback_failure_is_reported_and_does_not_follow_outside_symlink(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source).success is True
    (source / "payload.txt").write_text("v2", encoding="utf-8")
    real_replace = service._paths.replace

    def fail_publish_and_rollback(source_name, target_name):
        if source_name.startswith(".koder-stage-") and target_name == "demo":
            raise OSError("publish failed")
        if source_name.startswith(".koder-backup-") and target_name == "demo":
            raise OSError("restore failed")
        return real_replace(source_name, target_name)

    monkeypatch.setattr(service._paths, "replace", fail_publish_and_rollback)
    result = service.install_from_dir(source)

    assert result.success is False
    assert result.rollback_performed is False
    assert "rollback failed" in result.message


def test_target_swap_during_atomic_publish_replaces_symlink_entry_only(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("intact", encoding="utf-8")
    target = service.root / "demo"
    real_replace = service._paths.replace
    raced = False

    def race_publish(source_name, target_name):
        nonlocal raced
        if not raced and source_name.startswith(".koder-stage-") and target_name == "demo":
            raced = True
            target.symlink_to(outside, target_is_directory=True)
        return real_replace(source_name, target_name)

    monkeypatch.setattr(service._paths, "replace", race_publish)
    result = service.install_from_dir(source)

    assert result.success is False
    assert not target.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "intact"


def test_uninstall_cleanup_failure_restores_disabled_state_and_metadata(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    assert service.install_from_dir(source, scope="project").success is True
    assert service.disable("demo").success is True
    real_remove = service._paths.remove_entry_name

    def fail_backup_cleanup(name):
        if name.startswith(".koder-backup-"):
            raise OSError("locked backup")
        return real_remove(name)

    monkeypatch.setattr(service._paths, "remove_entry_name", fail_backup_cleanup)
    result = service.uninstall("demo")

    assert result.success is False
    restored = service.state_store.get("demo")
    assert restored is not None
    assert restored.enabled is False
    assert restored.scope == "project"
    assert (service.root / "demo" / "plugin.json").is_file()


def test_upgrade_post_delete_cleanup_error_keeps_committed_target_and_state(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo", version="1.0.0")
    (source / "payload.txt").write_text("v1", encoding="utf-8")
    assert service.install_from_dir(source, scope="project").success is True
    (source / "payload.txt").write_text("v2", encoding="utf-8")
    _write_plugin(source, "demo", version="2.0.0")
    real_remove = service._paths.remove_entry_name
    backup_removals = 0

    def delete_backup_then_raise(name):
        nonlocal backup_removals
        real_remove(name)
        if name.startswith(".koder-backup-"):
            backup_removals += 1
            if backup_removals == 2:
                raise OSError("post-delete backup cleanup error")

    monkeypatch.setattr(service._paths, "remove_entry_name", delete_backup_then_raise)

    result = service.install_from_dir(source, scope="user")

    assert result.success is True
    assert result.rollback_performed is False
    assert "after deletion" in result.message
    assert (service.root / "demo" / "payload.txt").read_text(encoding="utf-8") == "v2"
    state = service.state_store.get("demo")
    assert state is not None and state.enabled is True and state.scope == "user"
    assert not (service.root / ".koder-lifecycle-transaction.json").exists()


def test_uninstall_post_delete_cleanup_error_keeps_committed_absence(tmp_path, monkeypatch):
    service = PluginLifecycleService.for_test(tmp_path)
    source = tmp_path / "source"
    _write_plugin(source, "demo")
    assert service.install_from_dir(source, scope="project").success is True
    real_remove = service._paths.remove_entry_name
    backup_removals = 0

    def delete_backup_then_raise(name):
        nonlocal backup_removals
        real_remove(name)
        if name.startswith(".koder-backup-"):
            backup_removals += 1
            if backup_removals == 2:
                raise OSError("post-delete backup cleanup error")

    monkeypatch.setattr(service._paths, "remove_entry_name", delete_backup_then_raise)

    result = service.uninstall("demo")

    assert result.success is True
    assert result.rollback_performed is False
    assert "after deletion" in result.message
    assert not (service.root / "demo").exists()
    assert service.state_store.get("demo") is None
    assert not (service.root / ".koder-lifecycle-transaction.json").exists()


def test_recovery_commits_published_target_when_install_backup_is_orphaned(tmp_path):
    root = tmp_path / "installed_plugins"
    root.mkdir()
    target = root / "demo"
    _write_plugin(target, "demo", version="2.0.0")
    (root / "state.json").write_text(
        json.dumps({"demo": {"enabled": False, "scope": "project"}}), encoding="utf-8"
    )
    journal = {
        "operation": "install",
        "plugin_name": "demo",
        "stage_name": ".koder-stage-000000000000000000000000",
        "backup_name": ".koder-backup-111111111111111111111111",
        "phase": "target_published",
        "previous_state": {"enabled": False, "scope": "project"},
        "new_state": {"enabled": True, "scope": "user"},
    }
    (root / ".koder-lifecycle-transaction.json").write_text(json.dumps(journal), encoding="utf-8")

    service = PluginLifecycleService.for_test(tmp_path)

    assert (service.root / "demo" / "plugin.json").is_file()
    recovered = service.state_store.get("demo")
    assert recovered is not None and recovered.enabled is True and recovered.scope == "user"
    assert not (root / ".koder-lifecycle-transaction.json").exists()


def test_recovery_restores_orphan_backup_when_install_target_is_missing(tmp_path):
    root = tmp_path / "installed_plugins"
    root.mkdir()
    backup_name = ".koder-backup-333333333333333333333333"
    _write_plugin(root / backup_name, "demo", version="1.0.0")
    (root / "state.json").write_text(
        json.dumps({"demo": {"enabled": False, "scope": "project"}}), encoding="utf-8"
    )
    journal = {
        "operation": "install",
        "plugin_name": "demo",
        "stage_name": ".koder-stage-444444444444444444444444",
        "backup_name": backup_name,
        "phase": "backup_moved",
        "previous_state": {"enabled": False, "scope": "project"},
        "new_state": {"enabled": True, "scope": "user"},
        "target_existed": True,
    }
    (root / ".koder-lifecycle-transaction.json").write_text(json.dumps(journal), encoding="utf-8")

    service = PluginLifecycleService.for_test(tmp_path)

    assert (root / "demo" / "plugin.json").is_file()
    restored = service.state_store.get("demo")
    assert restored is not None and restored.enabled is False and restored.scope == "project"
    assert not (root / backup_name).exists()


def test_recovery_finishes_uninstall_with_orphan_backup(tmp_path):
    root = tmp_path / "installed_plugins"
    root.mkdir()
    backup_name = ".koder-backup-222222222222222222222222"
    _write_plugin(root / backup_name, "demo")
    (root / "state.json").write_text(json.dumps({}), encoding="utf-8")
    journal = {
        "operation": "uninstall",
        "plugin_name": "demo",
        "stage_name": None,
        "backup_name": backup_name,
        "phase": "state_removed",
        "previous_state": {"enabled": False, "scope": "project"},
        "new_state": None,
    }
    (root / ".koder-lifecycle-transaction.json").write_text(json.dumps(journal), encoding="utf-8")

    service = PluginLifecycleService.for_test(tmp_path)

    assert not (root / backup_name).exists()
    assert service.state_store.get("demo") is None
    assert not (root / ".koder-lifecycle-transaction.json").exists()
