# ruff: noqa: E402
"""Tests for notebook_edit security guards (H6).

Verifies that notebook_edit enforces read-before-write and rejects
edits to notebooks that haven't been read first.
"""

import asyncio
import json
import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.tools.file import get_file_state
from koder_agent.tools.notebook_edit import notebook_edit


def invoke_tool(tool, args_dict):
    """Helper to invoke a function tool synchronously."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


def _make_notebook(cells=None):
    """Create a minimal valid .ipynb structure."""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": cells
        or [
            {
                "cell_type": "code",
                "id": "abc123",
                "source": "print('hello')",
                "metadata": {},
                "outputs": [],
                "execution_count": None,
            }
        ],
    }


class TestNotebookEditReadBeforeWrite:
    """notebook_edit must reject edits on unread notebooks."""

    def test_replace_rejected_without_prior_read(self, tmp_path):
        """Replace on an unread notebook should return an error."""
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(_make_notebook()), encoding="utf-8")

        # Clear file state to ensure a clean slate
        get_file_state().clear()

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('pwned')",
            },
        )
        assert "read" in result.lower()
        # Verify the file was NOT modified
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        assert nb["cells"][0]["source"] == "print('hello')"

    def test_insert_rejected_without_prior_read(self, tmp_path):
        """Insert on an unread notebook should return an error."""
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(_make_notebook()), encoding="utf-8")

        get_file_state().clear()

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "insert",
                "new_source": "import os; os.system('rm -rf /')",
            },
        )
        assert "read" in result.lower()
        # Still only 1 cell
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        assert len(nb["cells"]) == 1

    def test_delete_rejected_without_prior_read(self, tmp_path):
        """Delete on an unread notebook should return an error."""
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(_make_notebook()), encoding="utf-8")

        get_file_state().clear()

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "delete",
            },
        )
        assert "read" in result.lower()
        # Cell still present
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        assert len(nb["cells"]) == 1

    def test_edit_allowed_after_read(self, tmp_path):
        """After recording a read, notebook_edit should succeed."""
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(_make_notebook()), encoding="utf-8")

        get_file_state().clear()
        # Simulate that the file was read (as read_file would do)
        resolved = str(nb_path.resolve())
        get_file_state().record_read(resolved)

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('updated')",
            },
        )
        assert "replaced" in result.lower() or "success" in result.lower()
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        assert nb["cells"][0]["source"] == "print('updated')"

    def test_symlink_resolved_for_read_check(self, tmp_path):
        """Symlink to notebook must resolve to the same path as what was read."""
        nb_path = tmp_path / "real.ipynb"
        nb_path.write_text(json.dumps(_make_notebook()), encoding="utf-8")

        link_path = tmp_path / "link.ipynb"
        link_path.symlink_to(nb_path)

        get_file_state().clear()
        # Record read of the real path
        get_file_state().record_read(str(nb_path.resolve()))

        # Edit via symlink should work since resolve() maps to the same path
        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(link_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('via link')",
            },
        )
        assert "replaced" in result.lower() or "success" in result.lower()
