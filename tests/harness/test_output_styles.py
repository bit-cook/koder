"""Unit tests for the output-style persona loader."""

import json

from koder_agent.harness.output_styles import (
    discover_output_styles,
    find_output_style,
    load_active_output_style_body,
    load_active_output_style_name,
    parse_output_style_file,
    project_output_styles_dir,
    save_active_output_style_name,
    user_output_styles_dir,
)


def _write_style(directory, filename, body, *, name=None, description=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    parts = []
    if name is not None or description is not None:
        parts.append("---")
        if name is not None:
            parts.append(f"name: {name}")
        if description is not None:
            parts.append(f"description: {description}")
        parts.append("---")
    parts.append(body)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def test_parse_output_style_file_reads_frontmatter_and_body(tmp_path):
    path = _write_style(
        tmp_path,
        "pirate.md",
        "Arrr, respond as a pirate.",
        name="Pirate",
        description="A swashbuckling persona",
    )

    style = parse_output_style_file(path, source="project")

    assert style is not None
    assert style.name == "Pirate"
    assert style.description == "A swashbuckling persona"
    assert style.body == "Arrr, respond as a pirate."
    assert style.source == "project"
    assert style.path == path


def test_parse_output_style_file_falls_back_to_stem_without_frontmatter(tmp_path):
    path = _write_style(tmp_path, "terse.md", "Be extremely terse.")

    style = parse_output_style_file(path, source="user")

    assert style is not None
    assert style.name == "terse"
    assert style.description == ""
    assert style.body == "Be extremely terse."


def test_discover_finds_project_style(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    _write_style(
        project_output_styles_dir(project),
        "pirate.md",
        "Talk like a pirate.",
        name="pirate",
        description="Pirate persona",
    )

    styles = discover_output_styles(project)

    assert "pirate" in styles
    assert styles["pirate"].source == "project"
    assert styles["pirate"].description == "Pirate persona"


def test_discover_project_overrides_user_on_name_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    _write_style(
        user_output_styles_dir(),
        "pirate.md",
        "USER pirate body.",
        name="pirate",
    )
    _write_style(
        project_output_styles_dir(project),
        "pirate.md",
        "PROJECT pirate body.",
        name="pirate",
    )

    styles = discover_output_styles(project)

    assert styles["pirate"].source == "project"
    assert styles["pirate"].body == "PROJECT pirate body."


def test_find_output_style_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    _write_style(
        project_output_styles_dir(project),
        "pirate.md",
        "Pirate body.",
        name="Pirate",
    )

    assert find_output_style("PIRATE", project) is not None
    assert find_output_style("pirate", project) is not None
    assert find_output_style("does-not-exist", project) is None


def test_save_and_load_active_style_persists_and_preserves_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_path = tmp_path / ".koder" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"outputStyle": {"theme": "dark"}}) + "\n", encoding="utf-8"
    )

    returned = save_active_output_style_name("pirate")

    assert returned == settings_path
    assert load_active_output_style_name() == "pirate"
    # Theme must be preserved alongside the new style key.
    saved = json.loads(settings_path.read_text())
    assert saved["outputStyle"]["theme"] == "dark"
    assert saved["outputStyle"]["style"] == "pirate"


def test_clear_active_style_removes_only_style_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    save_active_output_style_name("pirate")

    save_active_output_style_name(None)

    assert load_active_output_style_name() is None
    saved = json.loads((tmp_path / ".koder" / "settings.json").read_text())
    assert "style" not in saved["outputStyle"]


def test_load_active_body_returns_persona_body(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    _write_style(
        project_output_styles_dir(project),
        "pirate.md",
        "Respond only in pirate speak.",
        name="pirate",
    )
    save_active_output_style_name("pirate")

    body = load_active_output_style_body(project)

    assert body == "Respond only in pirate speak."


def test_load_active_body_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"

    assert load_active_output_style_body(project) is None


def test_load_active_body_none_when_style_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    save_active_output_style_name("ghost")

    # Active name points at a style that does not exist on disk.
    assert load_active_output_style_body(project) is None
