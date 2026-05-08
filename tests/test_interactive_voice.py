from koder_agent.core.interactive import _voice_prompt_text
from koder_agent.core.status_line import StatusLine


class _UsageTracker:
    def __init__(self):
        self.model = "gpt-5.4"
        self.session_usage = type(
            "_Usage",
            (),
            {
                "request_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_cost": 0.0,
                "current_context_tokens": 0,
            },
        )()


def test_status_line_can_show_transient_voice_notice():
    status_line = StatusLine(usage_tracker=_UsageTracker(), session_id="voice-session")
    status_line.set_notice("Voice: recording")
    fragments = status_line.get_formatted_text()
    assert any("Voice: recording" in fragment for _, fragment in fragments)


def test_status_line_does_not_truncate_voice_errors():
    status_line = StatusLine(usage_tracker=_UsageTracker(), session_id="voice-session")
    status_line.set_notice("Voice error: Error opening stream: no default input device")
    fragments = status_line.get_formatted_text()
    assert any(
        "Voice error: Error opening stream: no default input device" in fragment
        for _, fragment in fragments
    )


def test_voice_prompt_text_formats_buffer_messages():
    assert _voice_prompt_text("recording") == "[voice] Recording... Press Space or Enter to stop."
    assert _voice_prompt_text("transcribing") == "[voice] Transcribing..."
    assert _voice_prompt_text("cancelled") == "Voice cancelled."
    assert _voice_prompt_text("no_text") == "Voice transcription returned no text."
    assert _voice_prompt_text("result", "hello world") == "hello world"
    assert (
        _voice_prompt_text("error", "Error opening stream: no default input device")
        == "Voice error: Error opening stream: no default input device"
    )
