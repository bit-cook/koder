"""Cron expression validation, matching, and display helpers."""

from __future__ import annotations

from datetime import datetime

_FIELD_SPECS = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),
]


def validate_cron(expr: str) -> str | None:
    """Validate a 5-field cron expression. Returns an error string or None."""

    fields = expr.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields (M H DoM Mon DoW), got {len(fields)}"
    for index, (field, (name, min_val, max_val)) in enumerate(zip(fields, _FIELD_SPECS), start=1):
        error = _validate_field(field, name=name, min_val=min_val, max_val=max_val)
        if error:
            return f"Invalid cron field {index}: {field} ({error})"
    return None


def cron_matches_now(cron_expr: str, now: datetime) -> bool:
    """Return whether a validated 5-field cron expression matches ``now``."""

    if validate_cron(cron_expr) is not None:
        return False

    fields = cron_expr.strip().split()
    cron_dow = (now.weekday() + 1) % 7
    checks = [
        (fields[0], now.minute, 0, 59),
        (fields[1], now.hour, 0, 23),
        (fields[2], now.day, 1, 31),
        (fields[3], now.month, 1, 12),
        (fields[4], cron_dow, 0, 7),
    ]

    return all(
        field_matches(expr, value, min_val, max_val) for expr, value, min_val, max_val in checks
    )


def field_matches(expr: str, value: int, min_val: int, max_val: int) -> bool:
    """Check if a single validated cron field matches a value."""

    try:
        values = _field_values(expr, min_val=min_val, max_val=max_val)
    except ValueError:
        return False
    if max_val == 7 and value == 0 and 7 in values:
        return True
    return value in values


def human_schedule(cron: str) -> str:
    """Convert a cron expression to a rough human-readable schedule."""

    fields = cron.strip().split()
    if len(fields) != 5:
        return cron
    minute, hour, _dom, _month, dow = fields

    parts = []
    if dow != "*":
        day_names = {
            "0": "Sun",
            "1": "Mon",
            "2": "Tue",
            "3": "Wed",
            "4": "Thu",
            "5": "Fri",
            "6": "Sat",
            "7": "Sun",
        }
        days = [day_names.get(d, d) for d in dow.split(",")]
        parts.append(f"on {','.join(days)}")

    if hour != "*" and minute != "*":
        parts.append(f"at {hour}:{minute.zfill(2)}")
    elif hour != "*":
        parts.append(f"at {hour}:00")

    return " ".join(parts) if parts else cron


def _validate_field(expr: str, *, name: str, min_val: int, max_val: int) -> str | None:
    try:
        _field_values(expr, min_val=min_val, max_val=max_val)
    except ValueError as exc:
        return str(exc)
    return None


def _field_values(expr: str, *, min_val: int, max_val: int) -> set[int]:
    if expr == "*":
        return set(range(min_val, max_val + 1))

    values: set[int] = set()
    for part in expr.split(","):
        if not part:
            raise ValueError("empty list item")
        values.update(_part_values(part, min_val=min_val, max_val=max_val))
    return values


def _part_values(part: str, *, min_val: int, max_val: int) -> set[int]:
    range_part = part
    step = 1
    if "/" in part:
        range_part, raw_step = part.split("/", 1)
        if range_part != "*" and "-" not in range_part:
            raise ValueError("steps require * or an explicit range")
        step = _parse_ascii_int(raw_step, label="step")
        if step <= 0:
            raise ValueError("step must be greater than 0")
        if step > (max_val - min_val + 1):
            raise ValueError("step is outside field range")

    if range_part == "*":
        start, end = min_val, max_val
    elif "-" in range_part:
        raw_start, raw_end = range_part.split("-", 1)
        start = _parse_ascii_int(raw_start, label="range start")
        end = _parse_ascii_int(raw_end, label="range end")
        if start > end:
            raise ValueError("range start must be <= range end")
    else:
        value = _parse_ascii_int(range_part, label="value")
        _assert_in_range(value, min_val=min_val, max_val=max_val)
        return {value}

    _assert_in_range(start, min_val=min_val, max_val=max_val)
    _assert_in_range(end, min_val=min_val, max_val=max_val)
    return set(range(start, end + 1, step))


def _parse_ascii_int(raw: str, *, label: str) -> int:
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{label} must be an ASCII integer")
    return int(raw)


def _assert_in_range(value: int, *, min_val: int, max_val: int) -> None:
    if not min_val <= value <= max_val:
        raise ValueError(f"value must be between {min_val} and {max_val}")
