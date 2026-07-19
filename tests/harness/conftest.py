"""Shared fixtures for all harness tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _auto_approve_project_hooks(request):
    """Auto-approve project hooks in tests that don't explicitly test the trust gate.

    The C1 trust gate blocks project hooks until the user approves them.
    Existing tests predate this security check and expect project hooks to
    fire unconditionally, so we patch it to return True by default.

    Tests in test_project_hook_trust.py manage their own patches.
    """
    if (
        "test_project_hook_trust" in request.node.nodeid
        or "real_project_hook_trust" in request.fixturenames
    ):
        yield
        return
    with patch(
        "koder_agent.harness.hooks.project_approval.is_project_hooks_allowed",
        return_value=True,
    ):
        yield


@pytest.fixture
def real_project_hook_trust():
    """Opt a harness test into the real project-hook approval gate."""
