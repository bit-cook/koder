"""Read-only local code-intelligence tool."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from koder_agent.harness.code_intelligence import run_code_intelligence

from .compat import function_tool


class CodeIntelligenceModel(BaseModel):
    operation: str
    path: Optional[str] = None
    query: Optional[str] = None
    line: Optional[int] = None
    character: Optional[int] = None
    limit: int = 50


def code_intelligence(
    operation: str,
    path: Optional[str] = None,
    query: Optional[str] = None,
    line: Optional[int] = None,
    character: Optional[int] = None,
    limit: int = 50,
) -> str:
    """Inspect local code symbols, definitions, references, and syntax diagnostics.

    Supported operations: document_symbols, workspace_symbols, definition,
    references, diagnostics. CamelCase operation aliases such as
    goToDefinition, findReferences, documentSymbol, and workspaceSymbol are
    accepted for tool interoperability.

    Args:
        operation: One of document_symbols, workspace_symbols, definition, references, diagnostics
        path: Source file to inspect (required for all operations except workspace_symbols)
        query: Symbol name to search for (workspace_symbols only)
        line: 1-indexed line of the symbol (definition/references)
        character: 0-indexed column of the symbol (definition/references)
        limit: Maximum number of results to return
    """
    return run_code_intelligence(
        operation=operation,
        path=path,
        query=query,
        line=line,
        character=character,
        limit=limit,
    )


code_intelligence_tool = function_tool(code_intelligence)
