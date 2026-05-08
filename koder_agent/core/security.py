"""Security guards for the Koder Agent."""

import re
from pathlib import Path
from typing import Optional

from koder_agent.core.bash_security import analyze_command


class SecurityGuard:
    """Security guard to validate paths and inputs."""

    UI_PATTERN = re.compile(r"^[\s\S]{1,2000}$")
    FORBIDDEN_WORDS = {"rm -rf", "shutdown", "reboot"}

    @staticmethod
    def validate_command(command: str) -> Optional[str]:
        """Validate a shell command for safety.

        Delegates to the bash_security analyzer for comprehensive
        pattern detection covering ~25 attack vectors.

        Returns None if the command is safe, or a reason string if blocked.
        """
        analysis = analyze_command(command)
        if analysis.blocked:
            return analysis.reason
        return None

    @staticmethod
    def sanitize_path(path: str) -> str:
        """Sanitize a path for safe usage."""
        # Remove any null bytes
        path = path.replace("\0", "")

        # Normalize path separators
        path = path.replace("\\", "/")

        # Remove redundant separators
        while "//" in path:
            path = path.replace("//", "/")

        # Remove trailing separators
        path = path.rstrip("/")

        return path

    @staticmethod
    def check_file_size(path: str, max_size_mb: int = 50) -> Optional[str]:
        """Check if file size is within limits."""
        try:
            path_obj = Path(path)
            if path_obj.is_file():
                size_mb = path_obj.stat().st_size / (1024 * 1024)
                if size_mb > max_size_mb:
                    return f"File too large: {size_mb:.2f}MB (max: {max_size_mb}MB)"
            return None
        except Exception as e:
            return f"Error checking file size: {str(e)}"
