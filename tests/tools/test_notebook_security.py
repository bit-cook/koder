# ruff: noqa: E402
"""Tests for notebook_edit security guards (H6).

Verifies that notebook_edit enforces read-before-write and rejects
edits to notebooks that haven't been read first.
"""

import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.service import PermissionService
from koder_agent.tools.file import get_file_state
from koder_agent.tools.notebook_edit import notebook_edit
from koder_agent.tools.permission_context import (
    reset_tool_permission_context,
    set_tool_permission_context,
)


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

    def test_outside_target_cannot_be_spoofed_with_workspace_path_alias(self, tmp_path):
        """The FunctionTool and PermissionService reject the exact acceptEdits spoof."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        decoy = workspace / "decoy.ipynb"
        decoy.write_text(json.dumps(_make_notebook()), encoding="utf-8")
        outside = tmp_path / "outside.ipynb"
        original = json.dumps(_make_notebook())
        outside.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(outside.resolve()), content=original)
        service = PermissionService.default(
            mode=PermissionMode.ACCEPT_EDITS,
            workspace_root=workspace,
        )
        spoofed_arguments = {
            "path": str(decoy),
            "notebook_path": str(outside),
            "cell_index": 0,
            "operation": "replace",
            "new_source": "print('pwned')",
        }

        service_result = service.evaluate_tool_call("notebook_edit", spoofed_arguments)
        assert not service_result.allowed
        assert not service_result.requires_approval
        assert "unexpected path field" in service_result.reason

        token = set_tool_permission_context(service)
        try:
            result = invoke_tool(notebook_edit, spoofed_arguments)
        finally:
            reset_tool_permission_context(token)

        assert "undeclared argument" in result
        assert "path" in result
        assert outside.read_text(encoding="utf-8") == original
        assert json.loads(decoy.read_text(encoding="utf-8"))["cells"][0]["source"] == (
            "print('hello')"
        )

    def test_function_tool_schema_and_direct_invocation_reject_extra_path(self, tmp_path):
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")
        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), content=original)

        assert notebook_edit.params_json_schema["additionalProperties"] is False
        result = invoke_tool(
            notebook_edit,
            {
                "path": str(nb_path),
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('changed')",
            },
        )

        assert "undeclared argument" in result
        assert nb_path.read_text(encoding="utf-8") == original

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

    def test_stale_notebook_rejected_after_external_edit(self, tmp_path):
        """A notebook changed after its read must not be overwritten."""
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), content=original)
        external = _make_notebook()
        external["cells"][0]["source"] = "print('external')"
        nb_path.write_text(json.dumps(external), encoding="utf-8")

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('agent')",
            },
        )

        assert "modified since" in result.lower()
        assert json.loads(nb_path.read_text(encoding="utf-8"))["cells"][0]["source"] == (
            "print('external')"
        )

    def test_partial_notebook_read_rejected(self, tmp_path):
        """A partial notebook view is insufficient for a whole-file cell edit."""
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), is_partial=True)

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('agent')",
            },
        )

        assert "partially read" in result.lower()
        assert nb_path.read_text(encoding="utf-8") == original

    @pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX symlinks")
    def test_leaf_symlink_is_rejected(self, tmp_path):
        """Notebook edits must not follow a symlink at the requested leaf."""
        nb_path = tmp_path / "real.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        link_path = tmp_path / "link.ipynb"
        link_path.symlink_to(nb_path)

        get_file_state().clear()
        # Record read of the real path
        get_file_state().record_read(str(nb_path.resolve()))

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(link_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('via link')",
            },
        )

        assert "symlink" in result.lower()
        assert nb_path.read_text(encoding="utf-8") == original
        assert link_path.is_symlink()

    def test_atomic_write_failure_preserves_original(self, tmp_path, monkeypatch):
        """A failed replacement must leave the original notebook intact."""
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), content=original)

        def fail_replace(_source, _target):
            raise OSError("simulated replace failure")

        monkeypatch.setattr("koder_agent.tools.file.os.replace", fail_replace)

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('agent')",
            },
        )

        assert "simulated replace failure" in result.lower()
        assert nb_path.read_text(encoding="utf-8") == original

    def test_freshness_is_rechecked_immediately_before_atomic_write(self, tmp_path, monkeypatch):
        """A change during edit preparation is detected before replacement."""
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), content=original)
        external = _make_notebook()
        external["cells"][0]["source"] = "print('external')"

        def modify_after_checkpoint(_path):
            nb_path.write_text(json.dumps(external), encoding="utf-8")

        monkeypatch.setattr("koder_agent.tools.file.record_pre_edit", modify_after_checkpoint)
        monkeypatch.setattr(
            "koder_agent.tools.file._atomic_write_no_follow",
            lambda *_args, **_kwargs: pytest.fail("atomic write should not run"),
        )

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('agent')",
            },
        )

        assert "modified since" in result.lower()
        assert (
            json.loads(nb_path.read_text(encoding="utf-8"))["cells"][0]["source"]
            == "print('external')"
        )

    def test_notebook_edit_records_pre_edit_checkpoint(self, tmp_path, monkeypatch):
        """Notebook edits participate in the same rewind checkpoint flow as file edits."""
        nb_path = tmp_path / "test.ipynb"
        original = json.dumps(_make_notebook())
        nb_path.write_text(original, encoding="utf-8")

        get_file_state().clear()
        get_file_state().record_read(str(nb_path.resolve()), content=original)
        recorded = []
        monkeypatch.setattr("koder_agent.tools.file.record_pre_edit", recorded.append)

        result = invoke_tool(
            notebook_edit,
            {
                "notebook_path": str(nb_path),
                "cell_index": 0,
                "operation": "replace",
                "new_source": "print('updated')",
            },
        )

        assert "replaced" in result.lower()
        assert recorded == [str(nb_path.resolve())]
