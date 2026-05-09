"""Config tool — programmatic get/set of koder settings."""

from __future__ import annotations

import json
from typing import Any, Optional, Union

from koder_agent.config.manager import get_config_manager

from .compat import function_tool

# Supported settings mapped to type coercion
_SUPPORTED_SETTINGS: dict[str, type] = {
    "model.name": str,
    "model.provider": str,
    "model.base_url": str,
    "model.api_key": str,
    "model.reasoning_effort": str,
    "cli.session": str,
    "cli.stream": bool,
    "skills.enabled": bool,
    "harness.reasoning_display": str,
    "voice.enabled": bool,
    "voice.provider": str,
}


def _get_nested(obj: Any, dotpath: str) -> Any:
    """Get a value from a nested object using dot notation."""
    parts = dotpath.split(".")
    current = obj
    for part in parts:
        if hasattr(current, part):
            current = getattr(current, part)
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _set_nested(obj: Any, dotpath: str, value: Any) -> None:
    """Set a value on a nested object using dot notation."""
    parts = dotpath.split(".")
    current = obj
    for part in parts[:-1]:
        if hasattr(current, part):
            current = getattr(current, part)
        else:
            return
    setattr(current, parts[-1], value)


def _coerce_value(value: str, target_type: type) -> Any:
    """Coerce a string value to the target type."""
    if target_type is bool:
        return value.lower() in ("true", "1", "yes", "on")
    if target_type is int:
        return int(value)
    return value


# --- Plain implementation ---


def config_tool(setting: str, value: Optional[Union[str, bool, int]] = None) -> str:
    """Get or set a koder configuration setting."""
    if setting not in _SUPPORTED_SETTINGS:
        return json.dumps(
            {
                "success": False,
                "error": f"Unknown setting: {setting}. Supported: {', '.join(sorted(_SUPPORTED_SETTINGS))}",
            }
        )

    mgr = get_config_manager()
    config = mgr.load()

    # GET operation
    if value is None:
        current = _get_nested(config, setting)
        return json.dumps(
            {
                "success": True,
                "operation": "get",
                "setting": setting,
                "value": current,
            }
        )

    # SET operation
    target_type = _SUPPORTED_SETTINGS[setting]
    previous = _get_nested(config, setting)

    if isinstance(value, str):
        coerced = _coerce_value(value, target_type)
    else:
        coerced = value

    _set_nested(config, setting, coerced)
    mgr.save(config)

    return json.dumps(
        {
            "success": True,
            "operation": "set",
            "setting": setting,
            "previous_value": previous,
            "new_value": coerced,
        }
    )


# --- @function_tool wrapper ---

config_tool_fn = function_tool(config_tool)
