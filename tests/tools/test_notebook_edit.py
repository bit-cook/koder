"""Tests for Jupyter notebook editing tool."""

import asyncio
import json

import pytest

from koder_agent.tools.notebook_edit import notebook_edit


def invoke_tool(tool, args_dict):
    """Helper to invoke a function tool synchronously."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


def _make_notebook(cells):
    """Create a minimal .ipynb structure."""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}
        },
        "cells": cells,
    }


def _code_cell(source, cell_id="cell1"):
    return {
        "cell_type": "code",
        "id": cell_id,
        "source": source,
        "metadata": {},
        "outputs": [],
        "execution_count": None,
    }


def _markdown_cell(source, cell_id="md1"):
    return {"cell_type": "markdown", "id": cell_id, "source": source, "metadata": {}}


@pytest.fixture
def sample_notebook(tmp_path):
    nb = _make_notebook(
        [
            _code_cell("import os\nimport sys", "cell1"),
            _markdown_cell("# Header\nSome text", "md1"),
            _code_cell("print('hello')", "cell2"),
        ]
    )
    path = tmp_path / "test.ipynb"
    path.write_text(json.dumps(nb))
    return path


def test_replace_cell(sample_notebook):
    result = invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 0,
            "operation": "replace",
            "new_source": "import numpy as np",
        },
    )
    assert "replaced" in result.lower() or "updated" in result.lower()

    # Verify the change
    nb = json.loads(sample_notebook.read_text())
    assert nb["cells"][0]["source"] == "import numpy as np"


def test_insert_cell(sample_notebook):
    result = invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 1,
            "operation": "insert",
            "new_source": "# New cell",
            "cell_type": "markdown",
        },
    )
    assert "inserted" in result.lower()

    nb = json.loads(sample_notebook.read_text())
    assert len(nb["cells"]) == 4
    assert nb["cells"][1]["source"] == "# New cell"
    assert nb["cells"][1]["cell_type"] == "markdown"


def test_delete_cell(sample_notebook):
    result = invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 2,
            "operation": "delete",
        },
    )
    assert "deleted" in result.lower()

    nb = json.loads(sample_notebook.read_text())
    assert len(nb["cells"]) == 2


def test_invalid_index(sample_notebook):
    result = invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 99,
            "operation": "replace",
            "new_source": "x",
        },
    )
    assert "error" in result.lower() or "invalid" in result.lower()


def test_invalid_path():
    result = invoke_tool(
        notebook_edit,
        {
            "notebook_path": "/nonexistent/notebook.ipynb",
            "cell_index": 0,
            "operation": "replace",
            "new_source": "x",
        },
    )
    assert "error" in result.lower() or "not found" in result.lower()


def test_replace_preserves_metadata(sample_notebook):
    invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 0,
            "operation": "replace",
            "new_source": "import numpy",
        },
    )

    nb = json.loads(sample_notebook.read_text())
    assert nb["cells"][0]["cell_type"] == "code"
    assert nb["nbformat"] == 4


def test_insert_code_cell_default(sample_notebook):
    """Insert without cell_type should default to code."""
    invoke_tool(
        notebook_edit,
        {
            "notebook_path": str(sample_notebook),
            "cell_index": 0,
            "operation": "insert",
            "new_source": "x = 1",
        },
    )

    nb = json.loads(sample_notebook.read_text())
    assert nb["cells"][0]["cell_type"] == "code"
