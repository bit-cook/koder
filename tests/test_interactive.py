"""Tests for interactive prompt layout helpers."""

from koder_agent.core.interactive import (
    _get_completion_menu_height,
    _get_completion_menu_max_rows,
    _get_idle_prompt_spacer_height,
    _get_prompt_scroll_padding_rows,
)


def test_get_completion_menu_max_rows_reserves_frame_and_status_line():
    """Slash completion uses a short Claude-style list even with spare rows."""
    assert _get_completion_menu_max_rows(rows=24, has_status_line=True) == 8


def test_get_completion_menu_max_rows_reserves_frame_without_status_line():
    """The short list cap also applies when the status line is hidden."""
    assert _get_completion_menu_max_rows(rows=24, has_status_line=False) == 8


def test_get_completion_menu_max_rows_keeps_tiny_terminals_safe():
    """Small terminals still clamp to the rows left after fixed chrome."""
    assert _get_completion_menu_max_rows(rows=7, has_status_line=True) == 2


def test_get_completion_menu_max_rows_reserves_bottom_padding():
    """The completion menu should leave a blank row below the status line."""
    assert (
        _get_completion_menu_max_rows(
            rows=13,
            has_status_line=True,
            bottom_padding_rows=0,
        )
        == 8
    )
    assert _get_completion_menu_max_rows(rows=13, has_status_line=True) == 8
    assert _get_completion_menu_max_rows(rows=12, has_status_line=True) == 7


def test_get_completion_menu_height_caps_to_remaining_space():
    """Large command lists should be clipped to the visible completion rows."""
    height = _get_completion_menu_height(
        completion_count=98,
        max_available_height=24,
        max_visible_rows=20,
    )
    assert (height.min, height.preferred, height.max) == (20, 20, 20)


def test_get_completion_menu_height_returns_zero_without_matches():
    """No matches should collapse the completion menu entirely."""
    height = _get_completion_menu_height(
        completion_count=0,
        max_available_height=24,
        max_visible_rows=20,
    )
    assert (height.min, height.preferred, height.max) == (0, 0, 0)


def test_idle_prompt_spacer_height_is_flexible_not_terminal_sized():
    """Idle prompts should fill live screen space without writing fixed blank scrollback."""
    height = _get_idle_prompt_spacer_height()

    assert (height.min, height.preferred, height.weight) == (0, 0, 1)
    assert height.max > 1000


def test_prompt_scroll_padding_uses_only_rows_needed_for_prompt_chrome():
    """Idle prompt rendering should not inject a full-screen spacer into scrollback."""
    assert (
        _get_prompt_scroll_padding_rows(
            rows_below_cursor=0,
            input_rows=3,
            has_status_line=True,
            bottom_padding_rows=1,
        )
        == 5
    )


def test_prompt_scroll_padding_is_zero_when_terminal_already_has_room():
    assert (
        _get_prompt_scroll_padding_rows(
            rows_below_cursor=8,
            input_rows=3,
            has_status_line=True,
            bottom_padding_rows=1,
        )
        == 0
    )
