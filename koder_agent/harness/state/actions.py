"""Typed actions for the harness state store."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SetMode:
    mode: str


@dataclass(frozen=True)
class AppendNotification:
    message: str
