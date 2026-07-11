"""Fatal error rendering tests for the top-level CLI."""

from io import StringIO

import pytest
from rich.console import Console

from koder_agent import cli
from koder_agent.harness.config.schema import RuntimeConfig


def test_run_omits_pydantic_help_url_from_fatal_error(monkeypatch):
    """Pydantic documentation links should not leak into Koder fatal errors."""
    output = StringIO()
    monkeypatch.delenv("PYDANTIC_ERRORS_INCLUDE_URL", raising=False)
    monkeypatch.setattr(
        cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    async def fail_with_invalid_config():
        RuntimeConfig(model={"reasoning_effort": "turbo"})

    monkeypatch.setattr(cli, "main", fail_with_invalid_config)

    with pytest.raises(SystemExit) as exc_info:
        cli.run()

    assert exc_info.value.code == 1
    rendered = output.getvalue()
    assert "Fatal error: 1 validation error for RuntimeConfig" in rendered
    assert "model.reasoning_effort" in rendered
    assert "Input should be" in rendered
    assert "For further information visit" not in rendered
    assert "errors.pydantic.dev" not in rendered
