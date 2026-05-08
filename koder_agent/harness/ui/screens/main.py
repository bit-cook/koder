"""Main screen metadata for the harness UI."""

MAIN_SCREEN_ID = "main"
MAIN_SCREEN_SECTIONS = ["status_line", "prompt_buffer"]


def render_main_screen(*, mode: str) -> dict:
    return {
        "screen": MAIN_SCREEN_ID,
        "sections": list(MAIN_SCREEN_SECTIONS),
        "mode": mode,
    }
