"""Read-only local code-intelligence tool."""

from __future__ import annotations

from typing import Optional

from agents import function_tool
from pydantic import BaseModel

from koder_agent.harness.code_intelligence import run_code_intelligence


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
