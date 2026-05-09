"""Todo list management tools."""

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


class TodoStore:
    """Singleton store for todos to ensure shared state across all agents."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._todos = []  # Initialize on first creation only
        return cls._instance

    @property
    def todos(self) -> List[dict]:
        return self._todos

    @todos.setter
    def todos(self, value: List[dict]):
        self._todos = value


# Global singleton instance - created once at module load
_store = TodoStore()


@function_tool
def todo_read() -> str:
    """Read all todos from the list."""
    if not _store.todos:
        return "No todos found. The list is empty."

    return _format_todo_list(_store.todos)


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
    """Write/update the todo list."""
    # Convert TodoItem objects to dictionaries
    _store.todos = [todo.model_dump() for todo in todos]

    if not _store.todos:
        return "Todo list cleared."

    return _format_todo_list(_store.todos, title="Updated Plan")
