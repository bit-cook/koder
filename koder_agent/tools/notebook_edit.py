"""Jupyter notebook cell editing tool."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from .compat import function_tool
from .file import get_file_state


@function_tool
def notebook_edit(
    notebook_path: str,
    cell_index: int,
    operation: str,
    new_source: Optional[str] = None,
    cell_type: Optional[str] = None,
) -> str:
    """Edit cells in a Jupyter notebook (.ipynb file).

    Operations:
    - replace: Replace the source of an existing cell
    - insert: Insert a new cell at the given index
    - delete: Delete the cell at the given index

    Args:
        notebook_path: Path to the .ipynb file.
        cell_index: 0-based index of the cell to operate on.
        operation: One of 'replace', 'insert', 'delete'.
        new_source: New cell source (required for replace/insert).
        cell_type: Cell type for insert: 'code' or 'markdown'. Defaults to 'code'.
    """
    path = Path(notebook_path).resolve()
    if not path.exists():
        return f"Error: notebook not found: {notebook_path}"
    if not path.suffix == ".ipynb":
        return f"Error: not a notebook file: {notebook_path}"

    # Security: require that the notebook was read before editing (prevents
    # blind writes and ensures the agent has seen current content).
    fs = get_file_state()
    resolved_str = str(path)
    if not fs.has_been_read(resolved_str) and not fs.has_been_read(notebook_path):
        return (
            "Error: you must read the notebook with read_file before editing it. "
            "This ensures you have the current content and prevents accidental overwrites."
        )

    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"Error: invalid notebook JSON: {e}"

    cells = nb.get("cells", [])

    if operation == "replace":
        if cell_index < 0 or cell_index >= len(cells):
            return f"Error: invalid cell index {cell_index} (notebook has {len(cells)} cells)"
        if new_source is None:
            return "Error: new_source required for replace operation"
        cells[cell_index]["source"] = new_source
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"Cell {cell_index} replaced successfully."

    elif operation == "insert":
        if cell_index < 0 or cell_index > len(cells):
            return f"Error: invalid cell index {cell_index} for insert (notebook has {len(cells)} cells)"
        if new_source is None:
            return "Error: new_source required for insert operation"
        ct = cell_type or "code"
        new_cell = {
            "cell_type": ct,
            "id": str(uuid.uuid4())[:8],
            "source": new_source,
            "metadata": {},
        }
        if ct == "code":
            new_cell["outputs"] = []
            new_cell["execution_count"] = None
        cells.insert(cell_index, new_cell)
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"Cell inserted at index {cell_index} (type: {ct})."

    elif operation == "delete":
        if cell_index < 0 or cell_index >= len(cells):
            return f"Error: invalid cell index {cell_index} (notebook has {len(cells)} cells)"
        deleted = cells.pop(cell_index)
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"Cell {cell_index} deleted (was {deleted.get('cell_type', 'unknown')} cell)."

    else:
        return f"Error: unknown operation '{operation}'. Use 'replace', 'insert', or 'delete'."
