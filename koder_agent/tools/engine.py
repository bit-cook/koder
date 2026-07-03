"""Tool orchestration helpers.

Concurrent execution: ``ToolOrchestrator`` from ``tools/orchestration.py`` provides
read/write batching for concurrent tool execution. Currently the openai-agents
SDK handles tool execution sequentially. When batch execution support is added,
the orchestrator can be integrated here to run read-only tools concurrently.

Note: the former ``ToolEngine`` class was removed because it had no runtime call
site — tools are exposed to the agent via the SDK ``@function_tool`` decorators
(see ``get_all_tools`` in ``tools/__init__.py``), not through ``ToolEngine``.
"""

from .orchestration import ToolOrchestrator


def get_orchestrator() -> ToolOrchestrator:
    """Get a ToolOrchestrator instance for concurrent read-only batching.

    Example usage when SDK supports batch execution::

        orchestrator = get_orchestrator()
        results = await orchestrator.execute_batch(
            calls=[
                {"tool": "read_file", "args": {"path": "file1.py"}},
                {"tool": "read_file", "args": {"path": "file2.py"}},
            ],
            executor=executor_callable,
        )
    """
    return ToolOrchestrator()
