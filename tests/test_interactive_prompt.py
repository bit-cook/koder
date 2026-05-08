from koder_agent.core.interactive import (
    MIN_COMPLETION_MENU_COLUMNS,
    MIN_COMPLETION_MENU_ROWS_NO_STATUS,
    MIN_COMPLETION_MENU_ROWS_WITH_STATUS,
    MIN_STATUS_LINE_ROWS,
    _should_show_bottom_padding,
    _should_show_completion_menu,
    _should_show_status_line,
)


def test_completion_menu_hidden_when_terminal_too_narrow():
    assert (
        _should_show_completion_menu(
            columns=MIN_COMPLETION_MENU_COLUMNS - 1,
            rows=MIN_COMPLETION_MENU_ROWS_WITH_STATUS,
            has_status_line=True,
        )
        is False
    )


def test_completion_menu_hidden_when_terminal_too_short_with_status_line():
    assert (
        _should_show_completion_menu(
            columns=MIN_COMPLETION_MENU_COLUMNS,
            rows=MIN_COMPLETION_MENU_ROWS_WITH_STATUS - 1,
            has_status_line=True,
        )
        is False
    )


def test_completion_menu_hidden_when_terminal_too_short_without_status_line():
    assert (
        _should_show_completion_menu(
            columns=MIN_COMPLETION_MENU_COLUMNS,
            rows=MIN_COMPLETION_MENU_ROWS_NO_STATUS - 1,
            has_status_line=False,
        )
        is False
    )


def test_completion_menu_shown_when_terminal_large_enough():
    assert (
        _should_show_completion_menu(
            columns=MIN_COMPLETION_MENU_COLUMNS,
            rows=MIN_COMPLETION_MENU_ROWS_WITH_STATUS,
            has_status_line=True,
        )
        is True
    )


def test_status_line_hidden_when_terminal_too_short():
    assert _should_show_status_line(rows=MIN_STATUS_LINE_ROWS - 1) is False


def test_status_line_shown_when_terminal_large_enough():
    assert _should_show_status_line(rows=MIN_STATUS_LINE_ROWS) is True


def test_bottom_padding_hidden_only_for_tiny_terminals():
    assert _should_show_bottom_padding(rows=5) is True
    assert _should_show_bottom_padding(rows=4) is False
