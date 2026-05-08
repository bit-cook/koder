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
