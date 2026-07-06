"""Post-compact context repair: restore files after compaction."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .budget import estimate_text_tokens

logger = logging.getLogger(__name__)

# Upstream constants
MAX_FILE_RESTORE_COUNT = 5
MAX_FILE_RESTORE_TOKENS = 50_000


class PostCompactRepair:
    """Restores context after compaction by re-reading recently accessed files."""

    def collect_recently_accessed_files(self, messages: list[dict]) -> list[str]:
        """Extract file paths from read_file tool calls in messages (most recent first).

        Handles two item shapes:
        * Chat Completions: an assistant message carrying a ``tool_calls`` list,
          each entry a ``{"function": {"name", "arguments"}}`` dict.
        * Koder/Responses items: a top-level ``{"type": "function_call",
          "name": ..., "arguments": ...}`` item -- what koder actually persists,
          so without this branch the collector found nothing.
        """
        seen: set[str] = set()
        paths: list[str] = []

        def _consider(name, raw_arguments) -> None:
            if name != "read_file":
                return
            if isinstance(raw_arguments, dict):
                args = raw_arguments  # some producers store parsed args
            elif isinstance(raw_arguments, str):
                try:
                    args = json.loads(raw_arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    logger.debug(
                        "Failed to parse tool call arguments for file restore", exc_info=True
                    )
                    return
            else:
                return
            if not isinstance(args, dict):
                return
            fp = args.get("path") or args.get("file_path")
            if fp and fp not in seen:
                seen.add(fp)
                paths.append(fp)

        # Walk messages in reverse to get most recently accessed first
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            # Koder/Responses top-level function_call item shape.
            if msg.get("type") == "function_call":
                _consider(msg.get("name"), msg.get("arguments"))
            # Chat Completions assistant-message tool_calls shape.
            for tc in msg.get("tool_calls", []) or []:
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                _consider(func.get("name"), func.get("arguments"))
        return paths[:MAX_FILE_RESTORE_COUNT]

    async def build_file_restoration_attachments(
        self,
        file_paths: list[str],
        token_budget: int = MAX_FILE_RESTORE_TOKENS,
    ) -> list[dict]:
        """Re-read files and build restoration message attachments within token budget."""
        attachments: list[dict] = []
        tokens_used = 0

        for fp in file_paths[:MAX_FILE_RESTORE_COUNT]:
            path = Path(fp)
            if not path.exists() or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                content_tokens = estimate_text_tokens(content)
                if tokens_used + content_tokens > token_budget:
                    break
                attachments.append(
                    {
                        "role": "system",
                        "content": f"[Post-compact file restoration] {fp}:\n{content}",
                    }
                )
                tokens_used += content_tokens
            except OSError:
                logger.debug("Failed to read file for post-compact restore: %s", fp, exc_info=True)
                continue

        return attachments
