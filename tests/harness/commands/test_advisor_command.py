from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from koder_agent.harness.commands.advisor import (
    collect_advisor_review_context,
    resolve_advisor_model,
    run_advisor_review,
)
from koder_agent.harness.config.schema import RuntimeConfig
from koder_agent.harness.config.service import RuntimeConfigService


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_resolve_advisor_model_prefers_harness_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_service = RuntimeConfigService(config_path=tmp_path / ".koder" / "config.yaml")
    config = RuntimeConfig()
    config.harness.advisor_model = "gpt-5.2"
    config.model.provider = "openai"
    config_service.save(config)

    assert resolve_advisor_model() == "gpt-5.2"


def test_resolve_advisor_model_uses_provider_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_service = RuntimeConfigService(config_path=tmp_path / ".koder" / "config.yaml")
    config = RuntimeConfig()
    config.model.provider = "openai"
    config_service.save(config)

    assert resolve_advisor_model() == "gpt-5.1"


def test_resolve_advisor_model_rejects_unsupported_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_service = RuntimeConfigService(config_path=tmp_path / ".koder" / "config.yaml")
    config = RuntimeConfig()
    config.model.provider = "azure"
    config_service.save(config)

    with pytest.raises(RuntimeError, match="No default advisor model"):
        resolve_advisor_model()


def test_collect_advisor_review_context_includes_session_and_git(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    tracked = repo / "app.py"
    tracked.write_text("print('seed')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text("print(user_input)\n", encoding="utf-8")

    context = collect_advisor_review_context(
        cwd=repo,
        session_items=[
            {"role": "user", "content": "Please review the auth flow."},
            {"role": "assistant", "content": "I updated the auth module."},
        ],
        focus="Look for regressions",
    )

    assert context is not None
    assert "Please review the auth flow." in context.session_transcript
    assert "I updated the auth module." in context.session_transcript
    assert context.git_context is not None
    assert "print(user_input)" in context.git_context.diff_content
    assert context.focus == "Look for regressions"


def test_run_advisor_review_uses_stronger_model_and_full_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    tracked = repo / "service.py"
    tracked.write_text("def auth():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "service.py"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text("def auth(user_input):\n    return user_input\n", encoding="utf-8")

    captured: dict[str, object] = {}

    async def _fake_completion(messages, model=None):
        captured["messages"] = messages
        captured["model"] = model
        return "# Advisor Review\n\n## Assessment\n- Looks suspicious."

    monkeypatch.setattr("koder_agent.harness.commands.advisor.llm_completion", _fake_completion)

    output = asyncio.run(
        run_advisor_review(
            cwd=repo,
            session_items=[
                {"role": "user", "content": "Check auth and tests."},
                {"role": "assistant", "content": "I changed auth."},
            ],
            focus="Focus on auth and tests",
        )
    )

    assert output.startswith("# Advisor Review")
    assert str(captured["model"]).endswith("gpt-5.1")
    prompt = captured["messages"][-1]["content"]
    assert "Focus on auth and tests" in prompt
    assert "Check auth and tests." in prompt
    assert "I changed auth." in prompt
    assert "return user_input" in prompt


def test_run_advisor_review_allows_cross_provider_override_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    tracked = repo / "service.py"
    tracked.write_text("def auth():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "service.py"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text("def auth(user_input):\n    return user_input\n", encoding="utf-8")

    captured: dict[str, object] = {}

    async def _fake_completion(messages, model=None):
        captured["messages"] = messages
        captured["model"] = model
        return "# Advisor Review\n\n## Assessment\n- Cross-provider works."

    monkeypatch.setattr("koder_agent.harness.commands.advisor.llm_completion", _fake_completion)

    config = RuntimeConfig()
    config.model.provider = "openai"
    config.harness.advisor_model = "anthropic/claude-opus-4-1"

    output = asyncio.run(
        run_advisor_review(
            cwd=repo,
            session_items=[
                {"role": "user", "content": "Check auth and tests."},
            ],
            config=config,
        )
    )

    assert output.startswith("# Advisor Review")
    assert str(captured["model"]).startswith("litellm/anthropic/")
