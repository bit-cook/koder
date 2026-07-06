"""Tests that the active output-style persona is injected into the system prompt."""

import asyncio

from koder_agent.agentic.agent import create_dev_agent
from koder_agent.harness.output_styles import save_active_output_style_name


def _fake_snapshot(_model_override):
    return {
        "model_name": "litellm/claude/claude-sonnet-4-6",
        "api_key": "oauth-access-token",
        "base_url": None,
        "native_openai": False,
        "litellm_kwargs": {
            "model": "claude/claude-sonnet-4-6",
            "api_key": "oauth-access-token",
            "base_url": None,
            "extra_headers": {},
        },
    }


def _write_project_style(cwd, filename, body, *, name):
    styles_dir = cwd / ".koder" / "output-styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    (styles_dir / filename).write_text(f"---\nname: {name}\n---\n{body}\n", encoding="utf-8")


def test_active_persona_body_appended_to_system_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", _fake_snapshot)
    persona = "You are Blackbeard. Respond only in pirate speak, ye scurvy dog."
    _write_project_style(tmp_path, "pirate.md", persona, name="pirate")
    save_active_output_style_name("pirate")

    agent = asyncio.run(create_dev_agent([]))

    assert persona in agent.instructions


def test_no_persona_when_style_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", _fake_snapshot)
    persona = "You are Blackbeard. Respond only in pirate speak."
    _write_project_style(tmp_path, "pirate.md", persona, name="pirate")
    # Deliberately do NOT activate the style.

    agent = asyncio.run(create_dev_agent([]))

    assert persona not in agent.instructions


def test_persona_not_injected_when_instructions_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", _fake_snapshot)
    persona = "You are Blackbeard. Respond only in pirate speak."
    _write_project_style(tmp_path, "pirate.md", persona, name="pirate")
    save_active_output_style_name("pirate")

    agent = asyncio.run(create_dev_agent([], instructions_override="Subagent-only instructions."))

    # Subagents with an explicit override carry their own persona; the active
    # output style must not leak into them.
    assert agent.instructions == "Subagent-only instructions."
    assert persona not in agent.instructions
