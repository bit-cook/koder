import asyncio
import json
from types import SimpleNamespace

import pytest
import yaml

from koder_agent.harness.config.commands import handle_config_subcommand
from koder_agent.harness.config.settings_bundle import (
    export_settings_bundle,
    import_settings_bundle,
)


def test_settings_bundle_round_trips_user_and_project_files(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".koder" / "memory").mkdir(parents=True)
    (project / ".koder" / "memory").mkdir(parents=True)
    (project / ".koder" / "session-memory").mkdir(parents=True)

    (home / ".koder" / "config.yaml").write_text(
        yaml.safe_dump({"model": {"name": "gpt-4.1"}}, sort_keys=False),
        encoding="utf-8",
    )
    (home / ".koder" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["read"]}}),
        encoding="utf-8",
    )
    (home / ".koder" / "memory" / "user.md").write_text("user memory", encoding="utf-8")
    (project / ".koder" / "settings.json").write_text(
        json.dumps({"hooks": []}),
        encoding="utf-8",
    )
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps({"local": True}),
        encoding="utf-8",
    )
    (project / ".koder" / "memory" / "project.md").write_text(
        "project memory",
        encoding="utf-8",
    )
    (project / ".koder" / "session-memory" / "notes.md").write_text(
        "session notes",
        encoding="utf-8",
    )

    bundle = tmp_path / "settings.json"
    export_result = export_settings_bundle(bundle, home=home, cwd=project)

    assert export_result.file_count == 7
    assert export_result.skipped == []

    new_home = tmp_path / "new-home"
    new_project = tmp_path / "new-project"
    import_result = import_settings_bundle(bundle, home=new_home, cwd=new_project)

    assert import_result.written == 7
    assert import_result.unchanged == 0
    assert (new_home / ".koder" / "config.yaml").exists()
    assert json.loads((new_project / ".koder" / "settings.local.json").read_text()) == {
        "local": True
    }
    assert (new_home / ".koder" / "memory" / "user.md").read_text() == "user memory"
    assert (new_project / ".koder" / "memory" / "project.md").read_text() == "project memory"
    assert (new_project / ".koder" / "session-memory" / "notes.md").read_text() == ("session notes")


def test_settings_bundle_import_dry_run_does_not_write(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".koder").mkdir(parents=True)
    (home / ".koder" / "config.yaml").write_text("model:\n  name: gpt-4.1\n", encoding="utf-8")
    bundle = tmp_path / "settings.json"
    export_settings_bundle(bundle, home=home, cwd=project)

    target_home = tmp_path / "target-home"
    result = import_settings_bundle(bundle, home=target_home, cwd=project, dry_run=True)

    assert result.dry_run is True
    assert result.written == 1
    assert not (target_home / ".koder" / "config.yaml").exists()


def test_settings_bundle_import_rejects_unsafe_relative_path(tmp_path):
    bundle = tmp_path / "unsafe.json"
    content = "bad"
    bundle.write_text(
        json.dumps(
            {
                "format": "koder-settings-bundle",
                "version": 1,
                "files": [
                    {
                        "role": "user_memory",
                        "scope": "user",
                        "relative_path": "../escape.md",
                        "content": content,
                        "sha256": (
                            "2f05d4b689d270cafb02285f35f44866f7dc8a2d368a3f9d1124373eeab31fb1"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsafe bundle relative path"):
        import_settings_bundle(bundle, home=tmp_path / "home", cwd=tmp_path / "project")


def test_settings_bundle_import_refuses_symlink_targets(tmp_path):
    source_home = tmp_path / "source-home"
    (source_home / ".koder").mkdir(parents=True)
    (source_home / ".koder" / "config.yaml").write_text(
        "model:\n  name: gpt-4.1\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "settings.json"
    export_settings_bundle(bundle, home=source_home, cwd=tmp_path / "source-project")

    target_home = tmp_path / "target-home"
    outside = tmp_path / "outside.yaml"
    outside.write_text("model:\n  name: outside\n", encoding="utf-8")
    (target_home / ".koder").mkdir(parents=True)
    (target_home / ".koder" / "config.yaml").symlink_to(outside)

    with pytest.raises(ValueError, match="Refusing to import over symlink target"):
        import_settings_bundle(bundle, home=target_home, cwd=tmp_path / "target-project")


def test_config_export_import_subcommands(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (home / ".koder").mkdir(parents=True)
    (home / ".koder" / "config.yaml").write_text("model:\n  name: gpt-4.1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    bundle = tmp_path / "bundle.json"

    export_code = asyncio.run(
        handle_config_subcommand(
            SimpleNamespace(config_action="export", path=str(bundle), scope="all")
        )
    )
    export_output = capsys.readouterr().out

    assert export_code == 0
    assert "Exported settings bundle" in export_output
    assert "files: 1" in export_output

    (home / ".koder" / "config.yaml").unlink()
    import_code = asyncio.run(
        handle_config_subcommand(
            SimpleNamespace(config_action="import", path=str(bundle), scope="all", dry_run=False)
        )
    )
    import_output = capsys.readouterr().out

    assert import_code == 0
    assert "Imported settings bundle" in import_output
    assert "written: 1" in import_output
    assert (home / ".koder" / "config.yaml").exists()
