"""Todo list management tools."""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import List

from pydantic import BaseModel

from .compat import function_tool


class TodoModel(BaseModel):
    pass


class TodoItem(BaseModel):
    content: str
    status: str
    priority: str
    id: str


class TodoWriteModel(BaseModel):
    todos: List[TodoItem]


@dataclass(frozen=True)
class TodoRuntimeIdentity:
    """Identity of one in-memory todo owner."""

    session_id: str
    agent_id: str
    run_id: str

    @classmethod
    def direct(cls) -> "TodoRuntimeIdentity":
        return cls(session_id="__direct__", agent_id="direct", run_id="direct")


class TodoStore:
    """Thread-safe, in-memory todo state for one explicit runtime identity.

    Stores are intentionally not durable. A scheduler keeps its store for its
    lifetime, and callers may pass that same store to a replacement scheduler
    during an in-process session switch. A fresh process or unrelated scheduler
    starts with an empty store.
    """

    def __init__(self, identity: TodoRuntimeIdentity):
        self.identity = identity
        self._lock = threading.RLock()
        self._todos: list[dict] = []

    @property
    def todos(self) -> List[dict]:
        with self._lock:
            return [dict(todo) for todo in self._todos]

    @todos.setter
    def todos(self, value: List[dict]):
        with self._lock:
            self._todos = [dict(todo) for todo in value]

    def clear(self) -> None:
        self.todos = []


_todo_store_var: ContextVar[TodoStore | None] = ContextVar("koder_todo_store", default=None)


def set_todo_context(store: TodoStore) -> Token:
    """Publish one runtime's store to SDK-created tool tasks."""
    return _todo_store_var.set(store)


def reset_todo_context(token: Token) -> None:
    """Restore the previous todo runtime scope."""
    try:
        _todo_store_var.reset(token)
    except (LookupError, ValueError):
        _todo_store_var.set(None)


def get_todo_store() -> TodoStore:
    """Return the explicitly scoped store, failing closed when none exists."""
    store = _todo_store_var.get()
    if store is None:
        raise RuntimeError(
            "todo tools require an explicit runtime identity; this caller has no todo scope"
        )
    return store


def get_todo_store_or_none() -> TodoStore | None:
    """Return the current explicitly scoped store without creating fallback state."""
    return _todo_store_var.get()


def reset_todo_state_for_tests() -> None:
    """Clear the current test scope without creating process-global state."""
    current = _todo_store_var.get()
    if current is not None:
        current.clear()
    _todo_store_var.set(None)


@function_tool
def todo_read() -> str:
    """Read all todos from the list."""
    todos = get_todo_store().todos
    if not todos:
        return "No todos found. The list is empty."

    return _format_todo_list(todos)


def _format_todo_list(todos: List[dict], *, title: str = "Current Plan") -> str:
    """Format todo list in a compact plan style."""
    result = [title]
    for index, todo in enumerate(todos):
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        prefix = "  └ " if index == 0 else "    "
        marker = "✔" if status == "completed" else "□"

        result.append(f"{prefix}{marker} {content}")

    return "\n".join(result)


@function_tool
def todo_write(todos: List[TodoItem]) -> str:
    """Write/update the todo list.

    Replaces the entire list; pass an empty list to clear it.

    When to use:
      - Non-trivial work with 3+ distinct steps.
      - The user gives multiple tasks or requests in one message.
      - Multi-step work where the user benefits from visible progress.

    When NOT to use:
      - A single, trivial task - just do it.
      - Purely conversational or informational requests.

    Status discipline: mark exactly one item in_progress before starting
    it, and mark it completed IMMEDIATELY after finishing - do not batch
    several completions into one call at the end. NEVER mark an item
    completed while tests are failing, the implementation is partial, or
    errors remain unresolved; keep it in_progress and surface the blocker
    (add a new item for it if needed).

    Args:
        todos: Full todo list; each item has content (task text), status
            (pending, in_progress, or completed), priority (high, medium,
            or low), and a stable string id
    """
    # Convert TodoItem objects to dictionaries
    store = get_todo_store()
    store.todos = [todo.model_dump() for todo in todos]

    current_todos = store.todos
    if not current_todos:
        return "Todo list cleared."

    return _format_todo_list(current_todos, title="Updated Plan")
