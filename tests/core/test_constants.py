"""Tests for shared runtime constants."""

from koder_agent.core.constants import DEFAULT_MAX_TURNS, get_max_turns


def test_default_max_turns_is_5000():
    assert DEFAULT_MAX_TURNS == 5000


def test_get_max_turns_default(monkeypatch):
    monkeypatch.delenv("KODER_MAX_TURNS", raising=False)
    assert get_max_turns() == DEFAULT_MAX_TURNS


def test_get_max_turns_env_override(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TURNS", "123")
    assert get_max_turns() == 123


def test_get_max_turns_rejects_invalid_values(monkeypatch):
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv("KODER_MAX_TURNS", bad)
        assert get_max_turns() == DEFAULT_MAX_TURNS
