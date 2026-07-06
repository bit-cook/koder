"""Tests for the Claude-style working indicator."""

import random

from koder_agent.core.working_indicator import _WORDS, WorkingIndicator


def test_inactive_by_default():
    indicator = WorkingIndicator()
    assert not indicator.is_active
    assert indicator.status_text() == ""
    assert indicator.status_parts() == ("", "")


def test_finish_is_idempotent():
    indicator = WorkingIndicator()
    indicator.finish()
    assert not indicator.is_active
    indicator.begin(now=1.0)
    indicator.finish()
    indicator.finish()
    assert not indicator.is_active
    assert indicator.status_text(now=5.0) == ""


def test_status_text_contains_word_elapsed_and_esc_hint():
    indicator = WorkingIndicator()
    indicator.begin(now=100.0)
    text = indicator.status_text(now=112.4)
    assert "12s" in text
    assert any(word in text for word in _WORDS)
    assert "esc to interrupt" in text
    assert text.endswith(")")


def test_esc_hint_can_be_omitted():
    indicator = WorkingIndicator()
    indicator.begin(now=0.0)
    text = indicator.status_text(now=3.0, esc_hint=False)
    assert "esc to interrupt" not in text
    assert "(3s)" in text


def test_activity_shown_only_while_set():
    indicator = WorkingIndicator()
    indicator.begin(now=0.0)
    indicator.set_activity("run_shell")
    head, detail = indicator.status_parts(now=5.0)
    assert head
    assert detail == "(5s · run_shell · esc to interrupt)"

    indicator.set_activity(None)
    _, detail = indicator.status_parts(now=6.0)
    assert detail == "(6s · esc to interrupt)"


def test_frames_ping_pong():
    indicator = WorkingIndicator()
    indicator.begin(now=0.0)
    expected = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢", "·"]
    frames = [
        indicator.status_parts(now=i * WorkingIndicator.FRAME_SECONDS)[0].split()[0]
        for i in range(len(expected))
    ]
    assert frames == expected


def test_word_selection_is_random_choice(monkeypatch):
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    indicator = WorkingIndicator()
    indicator.begin(now=0.0)
    head, _ = indicator.status_parts(now=0.0)
    assert _WORDS[0] in head


def test_begin_restarts_clock_and_clears_activity():
    indicator = WorkingIndicator()
    indicator.begin(now=0.0)
    indicator.set_activity("run_shell")
    indicator.begin(now=50.0)
    head, detail = indicator.status_parts(now=52.0)
    assert head
    assert detail == "(2s · esc to interrupt)"
