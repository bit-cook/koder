"""Prompt buffer frame metadata for the harness UI."""

PROMPT_BUFFER_SECTION = "prompt_buffer"


def render_prompt_buffer(*, text: str = "", title: str = "Koder") -> dict:
    return {
        "section": PROMPT_BUFFER_SECTION,
        "title": title,
        "text": text,
    }
