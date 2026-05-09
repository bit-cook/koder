"""File search operation tools."""

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .compat import function_tool


class GlobModel(BaseModel):
    pattern: str
    path: Optional[str] = None


class GrepModel(BaseModel):
    pattern: str
    path: Optional[str] = None
    include: Optional[str] = None


@function_tool
def glob_search(pattern: str, path: Optional[str] = None) -> str:
    """Search for files matching a glob pattern."""
    try:
        base_path = Path(path) if path else Path.cwd()

        # Validate base path
        if not base_path.exists():
            return f"Path does not exist: {base_path}"

        if not base_path.is_dir():
            return f"Path is not a directory: {base_path}"

        # Use rglob for recursive search if pattern contains **
        if "**" in pattern:
            # For patterns like **/*, remove the leading **/
            actual_pattern = pattern[3:] if pattern.startswith("**/") else pattern
            all_matches = base_path.rglob(actual_pattern)
        else:
            all_matches = base_path.glob(pattern)

        # Filter out virtual environments and common ignore patterns
        matches = []
        for match in all_matches:
            # Skip hidden directories and common ignore patterns
            parts = match.parts
            if any(part.startswith(".") and part not in {".github", ".vscode"} for part in parts):
                continue
            if any(
                part in {"__pycache__", "node_modules", ".venv", "venv", ".git"} for part in parts
            ):
                continue
            matches.append(match)

        # Sort by modification time (newest first)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Limit results
        matches = matches[:100]

        if not matches:
            return "No matches found"

        # Format results
        results = []
        for match in matches:
            try:
                rel_path = match.relative_to(base_path)
                if match.is_dir():
                    results.append(f"[DIR]  {rel_path}/")
                else:
                    size = match.stat().st_size
                    results.append(f"[FILE] {rel_path} ({size} bytes)")
            except Exception:
                results.append(str(match))

        return "\n".join(results)

    except Exception as e:
        return f"Glob search error: {str(e)}"


@function_tool
def grep_search(
    pattern: str,
    path: Optional[str] = None,
    glob: Optional[str] = None,
    include: Optional[str] = None,
    output_mode: str = "files_with_matches",
    context: Optional[int] = None,
    type: Optional[str] = None,
    head_limit: int = 250,
    offset: int = 0,
    multiline: bool = False,
    case_insensitive: bool = False,
    context_after: Optional[int] = None,
    context_before: Optional[int] = None,
    line_numbers: bool = True,
) -> str:
    """Search for pattern in file contents using ripgrep.

    Args:
        pattern: Regex pattern to search for
        path: Base directory to search in (defaults to cwd)
        glob: File pattern filter (e.g., "*.py")
        include: Backward compat alias for glob
        output_mode: "files_with_matches" (default), "content", or "count"
        context: Number of context lines (-C flag)
        type: File type filter (e.g., "py", "js")
        head_limit: Maximum results to return (default 250)
        offset: Skip first N results (for pagination)
        multiline: Enable multiline mode
        case_insensitive: Case-insensitive search (-i flag)
        context_after: Lines of context after match (-A flag)
        context_before: Lines of context before match (-B flag)
        line_numbers: Show line numbers in content mode (default True)

    Returns:
        Formatted search results
    """
    try:
        # Find ripgrep
        rg_path = shutil.which("rg")
        if not rg_path:
            return (
                "Error: ripgrep (rg) is not installed.\n"
                "Please install ripgrep: https://github.com/BurntSushi/ripgrep#installation\n"
                "  macOS: brew install ripgrep\n"
                "  Ubuntu/Debian: apt install ripgrep\n"
                "  Windows: choco install ripgrep"
            )

        base_path = Path(path) if path else Path.cwd()

        # Validate base path
        if not base_path.exists():
            return f"Path does not exist: {base_path}"

        # Build ripgrep command
        cmd = [rg_path]

        # Basic flags
        cmd.extend(["--hidden", "--max-columns", "500"])

        # Exclude VCS directories
        for vcs_dir in [".git", ".svn", ".hg", ".bzr", ".jj", ".sl"]:
            cmd.extend(["--glob", f"!{vcs_dir}"])

        # Multiline mode
        if multiline:
            cmd.extend(["-U", "--multiline-dotall"])

        # Case sensitivity
        if case_insensitive:
            cmd.append("-i")

        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        elif output_mode == "content":
            # Line numbers enabled by default in content mode unless explicitly disabled
            if line_numbers:
                cmd.append("-n")

        # Context lines - -C takes precedence over -A/-B
        if context is not None:
            cmd.extend(["-C", str(context)])
        else:
            if context_after is not None:
                cmd.extend(["-A", str(context_after)])
            if context_before is not None:
                cmd.extend(["-B", str(context_before)])

        # Type filter
        if type:
            cmd.extend(["--type", type])

        # Glob filter (include is backward compat)
        glob_pattern = glob or include
        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])

        # Pattern - use -e flag to prevent pattern injection when pattern starts with dash
        if pattern.startswith("-"):
            cmd.extend(["-e", pattern])
        else:
            cmd.append(pattern)

        # Search path
        cmd.append(str(base_path))

        # Run ripgrep
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        # Handle exit codes: 0 = matches, 1 = no matches, 2+ = error
        if result.returncode >= 2:
            return f"Grep search error: {result.stderr.strip()}"

        if result.returncode == 1 or not result.stdout.strip():
            return "No matches found"

        output = result.stdout

        # Process output based on mode
        if output_mode == "files_with_matches":
            # Sort by mtime (newest first) and relativize paths
            file_lines = output.strip().split("\n")
            files = []
            for line in file_lines:
                if line:
                    file_path = Path(line)
                    try:
                        rel_path = file_path.relative_to(base_path)
                        mtime = file_path.stat().st_mtime
                        files.append((rel_path, mtime))
                    except (ValueError, OSError):
                        files.append((Path(line), 0))

            # Sort by mtime, newest first
            files.sort(key=lambda x: x[1], reverse=True)

            # Apply offset and head_limit
            files = files[offset : offset + head_limit]

            if not files:
                return "No matches found"

            return "\n".join(str(f[0]) for f in files)

        elif output_mode == "content":
            # Relativize paths in content output
            lines = output.split("\n")
            relativized_lines = []
            for line in lines:
                if not line:
                    continue
                # Try to extract and relativize file path
                if ":" in line:
                    parts = line.split(":", 1)
                    file_part = parts[0]
                    try:
                        file_path = Path(file_part)
                        if file_path.exists() and file_path.is_absolute():
                            rel_path = file_path.relative_to(base_path)
                            line = f"{rel_path}:{parts[1]}" if len(parts) > 1 else str(rel_path)
                    except (ValueError, OSError):
                        pass
                relativized_lines.append(line)

            # Apply offset and head_limit
            output_lines = relativized_lines[offset : offset + head_limit]
            return "\n".join(output_lines) if output_lines else "No matches found"

        elif output_mode == "count":
            # Parse count output (file:count format)
            lines = output.strip().split("\n")
            count_data = []

            for line in lines:
                if ":" in line:
                    file_part, count_part = line.rsplit(":", 1)
                    try:
                        count = int(count_part)
                        file_path = Path(file_part)
                        try:
                            rel_path = file_path.relative_to(base_path)
                            count_data.append((str(rel_path), count))
                        except ValueError:
                            count_data.append((file_part, count))
                    except ValueError:
                        continue

            # Apply offset and head_limit
            count_data = count_data[offset : offset + head_limit]

            if not count_data:
                return "No matches found"

            # Calculate total from sliced data only
            total = sum(count for _, count in count_data)

            result_lines = [f"{file}: {count}" for file, count in count_data]
            result_lines.append(f"\nTotal matches: {total}")

            return "\n".join(result_lines)

        return output.strip()

    except subprocess.TimeoutExpired:
        return "Grep search error: Search timed out after 30 seconds"
    except Exception as e:
        return f"Grep search error: {str(e)}"
