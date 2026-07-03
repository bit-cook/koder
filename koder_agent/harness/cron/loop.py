"""Parsing and rendering helpers for the /loop slash command."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from koder_agent.harness.cron.expression import human_schedule, validate_cron


class LoopSpecError(ValueError):
    """Raised when a /loop spec cannot be represented as a durable cron job."""


@dataclass(frozen=True)
class LoopSpec:
    cron: str
    prompt: str
    recurring: bool = True


LOOP_USAGE = (
    "Usage: /loop [list|delete <id>|once <cron> <prompt>|<cron> <prompt>|5m <prompt>|@every 5m <prompt>]\n"
    "Examples:\n"
    "  /loop @every 5m check build\n"
    "  /loop 0 9 * * * morning standup\n"
    "  /loop once 30 14 * * 1 monday review"
)

_ONE_SHOT_PREFIXES = {"once", "--once", "run-once", "--run-once"}
_DURATION_RE = re.compile(r"^(?P<count>[0-9]+)(?P<unit>[smhd])$")
_ASCII_INT_RE = re.compile(r"^[0-9]+$")


def parse_loop_spec(args: Sequence[str]) -> LoopSpec:
    """Parse /loop arguments into Koder's durable cron representation."""

    tokens = [token.strip() for token in args if token.strip()]
    if not tokens:
        raise LoopSpecError("A /loop job needs a schedule and a prompt.")

    recurring = True
    if tokens[0].lower() in _ONE_SHOT_PREFIXES:
        recurring = False
        tokens = tokens[1:]
        if not tokens:
            raise LoopSpecError("A one-shot /loop job needs a schedule and a prompt.")

    schedule = tokens[0]
    schedule_lower = schedule.lower()
    if schedule_lower == "@after-turn":
        raise LoopSpecError(
            "unsupported schedule: @after-turn is thread-local and is not supported "
            "by Koder's durable cron scheduler."
        )

    if schedule_lower == "@every":
        if len(tokens) < 3:
            raise LoopSpecError("A /loop @every job needs an interval and a prompt.")
        cron = _cron_for_every_literal(tokens[1])
        prompt = " ".join(tokens[2:]).strip()
        return _build_spec(cron, prompt, recurring)

    if schedule_lower.startswith("@every:"):
        raw_seconds = schedule_lower.removeprefix("@every:")
        if not _ASCII_INT_RE.fullmatch(raw_seconds):
            raise LoopSpecError(
                f"unsupported schedule: invalid @every seconds value {raw_seconds!r}."
            )
        cron = _cron_for_seconds(int(raw_seconds))
        prompt = " ".join(tokens[1:]).strip()
        return _build_spec(cron, prompt, recurring)

    if _DURATION_RE.match(schedule_lower):
        if len(tokens) < 2:
            raise LoopSpecError("A /loop interval job needs a prompt.")
        cron = _cron_for_every_literal(schedule_lower)
        prompt = " ".join(tokens[1:]).strip()
        return _build_spec(cron, prompt, recurring)

    if len(tokens) < 6:
        raise LoopSpecError("A /loop cron job needs 5 cron fields followed by a prompt.")
    cron = " ".join(tokens[:5])
    error = validate_cron(cron)
    if error:
        raise LoopSpecError(f"Invalid cron expression: {error}")
    prompt = " ".join(tokens[5:]).strip()
    return _build_spec(cron, prompt, recurring)


def _build_spec(cron: str, prompt: str, recurring: bool) -> LoopSpec:
    if not prompt:
        raise LoopSpecError("A /loop job needs a prompt.")
    return LoopSpec(cron=cron, prompt=prompt, recurring=recurring)


def _cron_for_every_literal(literal: str) -> str:
    match = _DURATION_RE.match(literal.strip().lower())
    if not match:
        raise LoopSpecError(
            f"unsupported schedule: expected @every interval like 5m, 2h, or 1d, got {literal!r}."
        )
    count = int(match.group("count"))
    unit = match.group("unit")
    seconds_per_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return _cron_for_seconds(count * seconds_per_unit)


def _cron_for_seconds(seconds: int) -> str:
    if seconds <= 0:
        raise LoopSpecError("unsupported schedule: @every interval must be positive.")
    if seconds < 60:
        raise LoopSpecError(
            "unsupported schedule: sub-minute @every intervals are not supported by "
            "Koder's durable cron scheduler."
        )
    if seconds % 60 != 0:
        raise LoopSpecError(
            "unsupported schedule: @every intervals must align to whole minutes for durable cron."
        )

    minutes = seconds // 60
    if minutes < 60:
        if 60 % minutes != 0:
            raise LoopSpecError(
                "unsupported schedule: this @every interval cannot be represented as a 5-field cron expression."
            )
        return "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours < 24:
            if 24 % hours != 0:
                raise LoopSpecError(
                    "unsupported schedule: this @every interval cannot be represented as a 5-field cron expression."
                )
            return "0 * * * *" if hours == 1 else f"0 */{hours} * * *"
        if hours == 24:
            days = hours // 24
            return "0 0 * * *" if days == 1 else f"0 0 */{days} * *"

    raise LoopSpecError(
        "unsupported schedule: this @every interval cannot be represented as a 5-field cron expression."
    )


def format_loop_jobs(jobs: list[dict], *, empty_message: str | None = None) -> str:
    """Render stored cron jobs in the /loop command format."""

    if not jobs:
        return empty_message or f"No loop jobs.\n{LOOP_USAGE}"

    lines = [f"Loop jobs ({len(jobs)}):"]
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            lines.extend([f"  - index: {index}", "    malformed: expected object"])
            continue
        cron_expr = str(job.get("cron") or job.get("expression") or "?")
        prompt = str(job.get("prompt") or "")[:80]
        recurring = bool(job.get("recurring", True))
        lines.extend(
            [
                f"  - id: {job.get('id', '?')}",
                f"    cron: {cron_expr}",
                f"    human_schedule: {human_schedule(cron_expr)}",
                f"    recurring: {str(recurring).lower()}",
                f"    prompt: {prompt}",
            ]
        )
    return "\n".join(lines)
