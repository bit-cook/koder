"""Lower-priority internal and debug command descriptors."""

from .debug_commands import DEBUG_COMMAND_SPECS
from .diagnostic_commands import DIAGNOSTIC_COMMAND_SPECS

__all__ = ["DEBUG_COMMAND_SPECS", "DIAGNOSTIC_COMMAND_SPECS"]
