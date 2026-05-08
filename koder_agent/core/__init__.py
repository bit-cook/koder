"""Core components for Koder Agent."""

from __future__ import annotations

from importlib import import_module

__all__ = ["EnhancedSQLiteSession", "AgentScheduler", "SecurityGuard"]


def __getattr__(name: str):
    if name == "scheduler":
        return import_module(".scheduler", __name__)
    if name == "security":
        return import_module(".security", __name__)
    if name == "session":
        return import_module(".session", __name__)
    if name == "AgentScheduler":
        return import_module(".scheduler", __name__).AgentScheduler
    if name == "SecurityGuard":
        return import_module(".security", __name__).SecurityGuard
    if name == "EnhancedSQLiteSession":
        return import_module(".session", __name__).EnhancedSQLiteSession
    raise AttributeError(name)
