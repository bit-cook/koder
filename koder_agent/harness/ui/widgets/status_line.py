"""Status line frame metadata for the harness UI."""

STATUS_LINE_SECTION = "status_line"


def render_status_line(
    *, mode: str, session_id: str | None = None, model: str | None = None
) -> dict:
    return {
        "section": STATUS_LINE_SECTION,
        "mode": mode,
        "session_id": session_id,
        "model": model,
    }
