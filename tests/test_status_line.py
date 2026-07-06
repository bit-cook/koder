from __future__ import annotations

import json

from koder_agent.core.status_line import StatusLine


class _UsageTracker:
    def __init__(self):
        self.model = "gpt-5.4"
        self.session_usage = type(
            "_Usage",
            (),
            {
                "request_count": 1,
                "input_tokens": 12,
                "output_tokens": 8,
                "total_cost": 0.01,
                "last_input_tokens": 12,
                "last_output_tokens": 8,
                "current_context_tokens": 40,
            },
        )()


def test_status_line_uses_configured_command_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_path = tmp_path / ".koder" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "statusLine": {
                    "type": "command",
                    "command": 'python -c \'import sys, json; data=json.load(sys.stdin); print("custom:" + data["model"]["display_name"])\'',
                    "padding": 2,
                }
            }
        ),
        encoding="utf-8",
    )

    status_line = StatusLine(usage_tracker=_UsageTracker(), session_id="custom-status-session")
    fragments = status_line.get_formatted_text()
    rendered = "".join(fragment for _, fragment in fragments)

    assert "  custom:gpt-5.4" in rendered
    assert "Model:" not in rendered


def test_status_line_compacts_default_output_to_terminal_width(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shutil.get_terminal_size", lambda fallback: type("S", (), {"columns": 80})()
    )

    status_line = StatusLine(
        usage_tracker=_UsageTracker(),
        session_id="2026-05-05T19:49:00-long-session-id",
    )
    fragments = status_line.get_formatted_text()
    rendered = "".join(fragment for _, fragment in fragments)

    assert len(rendered) <= 80
    assert "M: " in rendered
    assert "Dir: " in rendered
    assert "Tok: " in rendered


def _make_tracker(model="gpt-5.4", **usage_kwargs):
    """Build a UsageTracker-like double whose summary() reflects usage_kwargs."""
    from koder_agent.core.usage_tracker import UsageTracker

    tracker = UsageTracker()
    tracker._model = model
    for key, value in usage_kwargs.items():
        setattr(tracker.session_usage, key, value)
    return tracker


def _wide_terminal(monkeypatch, columns=160):
    monkeypatch.setattr(
        "shutil.get_terminal_size", lambda fallback: type("S", (), {"columns": columns})()
    )


def test_token_cost_segment_known_pricing(monkeypatch):
    tracker = _make_tracker(input_tokens=40000, output_tokens=5000, cache_read_tokens=12000)
    tracker._cached_costs = (0.000003, 0.000009)  # known pricing
    tracker.session_usage.total_cost = 0.14

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    segment = status_line._format_token_cost_segment()

    assert segment.startswith("▽ ")
    assert "45k tok" in segment
    assert "(12k cached)" in segment
    assert "~$0.14" in segment
    assert "$?" not in segment


def test_token_cost_segment_unknown_pricing(monkeypatch):
    tracker = _make_tracker(input_tokens=40000, output_tokens=5000, cache_read_tokens=12000)
    tracker._cached_costs = (0.0, 0.0)  # subscription/OAuth: pricing unknown
    tracker.session_usage.total_cost = 0.0

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    segment = status_line._format_token_cost_segment()

    assert "45k tok" in segment
    assert "(12k cached)" in segment
    # Cost marked unavailable, not a misleading $0.00.
    assert "$?" in segment
    assert "$0.00" not in segment


def test_token_cost_segment_omits_cached_when_zero():
    tracker = _make_tracker(input_tokens=1000, output_tokens=500, cache_read_tokens=0)
    tracker._cached_costs = (0.000003, 0.000009)
    tracker.session_usage.total_cost = 0.01

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    segment = status_line._format_token_cost_segment()
    assert "cached" not in segment


def test_absolute_token_warning_fires_past_limit():
    tracker = _make_tracker()
    status_line = StatusLine(usage_tracker=tracker, session_id="s")

    # Default 200k threshold.
    assert status_line.absolute_token_warning(150_000) is None
    warning = status_line.absolute_token_warning(250_000)
    assert warning is not None
    assert "250k" in warning
    assert "200k" in warning


def test_absolute_token_warning_respects_explicit_limit():
    tracker = _make_tracker()
    status_line = StatusLine(usage_tracker=tracker, session_id="s")

    assert status_line.absolute_token_warning(5_000, limit=10_000) is None
    assert status_line.absolute_token_warning(15_000, limit=10_000) is not None


def test_absolute_token_warning_env_override(monkeypatch):
    monkeypatch.setenv("KODER_TOKEN_WARN_LIMIT", "50000")
    tracker = _make_tracker()
    status_line = StatusLine(usage_tracker=tracker, session_id="s")

    # 60k exceeds the overridden 50k limit even though below the 200k default.
    assert status_line.absolute_token_warning(60_000) is not None
    assert status_line.absolute_token_warning(40_000) is None


def test_wide_statusline_renders_token_cost_segment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _wide_terminal(monkeypatch, columns=200)

    tracker = _make_tracker(
        input_tokens=40000,
        output_tokens=5000,
        cache_read_tokens=12000,
        request_count=3,
        current_context_tokens=45000,
    )
    tracker._cached_costs = (0.000003, 0.000009)
    tracker.session_usage.total_cost = 0.14

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    fragments = status_line.get_formatted_text()
    rendered = "".join(fragment for _, fragment in fragments)

    assert "▽" in rendered
    assert "45k tok" in rendered
    assert "(12k cached)" in rendered
    assert "~$0.14" in rendered


def test_wide_statusline_renders_absolute_token_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _wide_terminal(monkeypatch, columns=220)
    # Force a huge context window so the percentage warning would NOT trigger,
    # proving the absolute-threshold warning is independent of context %.
    monkeypatch.setattr(
        "koder_agent.core.status_line.get_context_window_size", lambda model: 2_000_000
    )

    tracker = _make_tracker(
        input_tokens=250000,
        output_tokens=10000,
        request_count=5,
        current_context_tokens=250000,
    )
    tracker._cached_costs = (0.000003, 0.000009)
    tracker.session_usage.total_cost = 1.23

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    fragments = status_line.get_formatted_text()
    rendered = "".join(fragment for _, fragment in fragments)

    assert "⚠" in rendered
    assert "250k tokens" in rendered
    # Context percentage is tiny (250k / 2M = 12.5%) so this warning is purely absolute.
    assert "(12.5%)" in rendered


def test_wide_statusline_marks_cost_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _wide_terminal(monkeypatch, columns=200)

    tracker = _make_tracker(
        input_tokens=40000,
        output_tokens=5000,
        request_count=3,
        current_context_tokens=45000,
    )
    tracker._cached_costs = (0.0, 0.0)  # unknown pricing
    tracker.session_usage.total_cost = 0.0

    status_line = StatusLine(usage_tracker=tracker, session_id="s")
    fragments = status_line.get_formatted_text()
    rendered = "".join(fragment for _, fragment in fragments)

    assert "$?" in rendered
