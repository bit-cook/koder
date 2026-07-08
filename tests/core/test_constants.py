"""Tests for shared runtime constants."""

from koder_agent.core.constants import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TURN_TIMEOUT,
    get_max_turns,
    get_turn_timeout,
)


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


# ---------------------------------------------------------------------------
# Turn timeout (M2)
# ---------------------------------------------------------------------------


def test_default_turn_timeout_is_600():
    assert DEFAULT_TURN_TIMEOUT == 600


def test_get_turn_timeout_default(monkeypatch):
    monkeypatch.delenv("KODER_TURN_TIMEOUT", raising=False)
    assert get_turn_timeout() == float(DEFAULT_TURN_TIMEOUT)


def test_get_turn_timeout_env_override(monkeypatch):
    monkeypatch.setenv("KODER_TURN_TIMEOUT", "300")
    assert get_turn_timeout() == 300.0


def test_get_turn_timeout_zero_disables(monkeypatch):
    monkeypatch.setenv("KODER_TURN_TIMEOUT", "0")
    assert get_turn_timeout() == 0.0


def test_get_turn_timeout_rejects_invalid_values(monkeypatch):
    for bad in ("", "abc", "-5"):
        monkeypatch.setenv("KODER_TURN_TIMEOUT", bad)
        assert get_turn_timeout() == float(DEFAULT_TURN_TIMEOUT)
