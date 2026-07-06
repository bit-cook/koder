"""Integration tests for the /output-style persona sub-commands.

These exercise the loader through the live command handler, ensuring that the
persona (``set`` / ``unset`` / ``styles``) concern coexists with the existing
theme/color/vim/statusline behaviour under the same command.
"""

import asyncio
import json
from types import SimpleNamespace

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


def _run(command, *, handler, scheduler):
    return asyncio.run(handler.handle_slash_input(command, scheduler=scheduler))


class _ResettableScheduler(SimpleNamespace):
    async def reset_agent(self):
        self.reset_count = getattr(self, "reset_count", 0) + 1
        self.dev_agent = None
        self._agent_initialized = False


def _write_project_style(cwd, filename, body, *, name, description=None):
    styles_dir = cwd / ".koder" / "output-styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}"]
    if description is not None:
        lines.append(f"description: {description}")
    lines += ["---", body]
    (styles_dir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_output_style_lists_discovered_project_style(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(
        tmp_path,
        "pirate.md",
        "Talk like a pirate.",
        name="pirate",
        description="A swashbuckling persona",
    )
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    output = _run("/output-style styles", handler=handler, scheduler=None)

    assert "output-style styles:" in output
    assert "pirate [project]: A swashbuckling persona" in output
    assert "active: none" in output


def test_output_style_set_persists_and_reloads_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="pirate")
    scheduler = _ResettableScheduler(reset_count=0)
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    set_output = _run("/output-style set pirate", handler=handler, scheduler=scheduler)

    assert "style set to pirate" in set_output
    assert "source: project" in set_output
    assert "agent_reloaded: True" in set_output
    assert scheduler.reset_count == 1

    settings = json.loads((tmp_path / "home" / ".koder" / "settings.json").read_text())
    assert settings["outputStyle"]["style"] == "pirate"

    # A fresh handler must observe the persisted style in status output.
    status = _run(
        "/output-style",
        handler=HarnessInteractiveCommandHandler(emit_console=False),
        scheduler=None,
    )
    assert "style: pirate" in status


def test_output_style_set_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="Pirate")
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    out = _run("/output-style set PIRATE", handler=handler, scheduler=None)

    assert "style set to Pirate" in out
    settings = json.loads((tmp_path / "home" / ".koder" / "settings.json").read_text())
    assert settings["outputStyle"]["style"] == "Pirate"


def test_output_style_set_unknown_style_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="pirate")
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    out = _run("/output-style set nope", handler=handler, scheduler=None)

    assert "unknown style nope" in out
    assert "available: pirate" in out
    # Nothing should be persisted for an unknown style.
    settings_path = tmp_path / "home" / ".koder" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        assert settings.get("outputStyle", {}).get("style") is None


def test_output_style_unset_clears_active_style(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="pirate")
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    _run("/output-style set pirate", handler=handler, scheduler=None)
    out = _run("/output-style unset", handler=handler, scheduler=None)

    assert "style cleared" in out
    settings = json.loads((tmp_path / "home" / ".koder" / "settings.json").read_text())
    assert "style" not in settings.get("outputStyle", {})


def test_output_style_theme_still_works_and_is_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="pirate")
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    theme_out = _run("/output-style theme dark", handler=handler, scheduler=None)
    set_out = _run("/output-style set pirate", handler=handler, scheduler=None)

    assert "theme: dark" in theme_out
    assert "style set to pirate" in set_out

    settings = json.loads((tmp_path / "home" / ".koder" / "settings.json").read_text())
    # Both concerns persist side by side.
    assert settings["outputStyle"]["theme"] == "dark"
    assert settings["outputStyle"]["style"] == "pirate"

    status = _run("/output-style", handler=handler, scheduler=None)
    assert "theme: dark" in status
    assert "style: pirate" in status


def test_output_style_reset_clears_style_and_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    _write_project_style(tmp_path, "pirate.md", "Talk like a pirate.", name="pirate")
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    _run("/output-style theme dark", handler=handler, scheduler=None)
    _run("/output-style set pirate", handler=handler, scheduler=None)
    reset_out = _run("/output-style reset", handler=handler, scheduler=None)

    assert "style: none" in reset_out
    assert "theme: adaptive" in reset_out

    settings = json.loads((tmp_path / "home" / ".koder" / "settings.json").read_text())
    assert settings["outputStyle"].get("theme") == "adaptive"
    assert "style" not in settings["outputStyle"]


def test_output_style_set_requires_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    out = _run("/output-style set", handler=handler, scheduler=None)

    assert out == "Usage: /output-style set <name>"


def test_output_style_styles_empty_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    out = _run("/output-style styles", handler=handler, scheduler=None)

    assert "(no output styles found)" in out
    assert "output-styles/" in out
