"""Deterministic retrieval for stored memory files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import estimate_text_tokens
from .memory_files import ParsedMemoryFile, parse_memory_file


@dataclass(frozen=True)
class RetrievedMemory:
    """A retrieved memory file with parsed contents."""

    path: Path
    parsed: ParsedMemoryFile
    score: int


@dataclass(frozen=True)
class RetrievalResult:
    """Result of a memory retrieval query."""

    memories: list[RetrievedMemory]
    token_count: int


def _scan_memory_files(memory_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for memory_dir in memory_dirs:
        if not memory_dir.exists():
            continue
        files.extend(sorted(memory_dir.rglob("*.md")))
    return files


def _score_memory(query_terms: list[str], parsed: ParsedMemoryFile) -> int:
    haystack = " ".join(
        part for part in [parsed.description or "", parsed.body, parsed.memory_type or ""] if part
    ).lower()
    return sum(1 for term in query_terms if term in haystack)


def retrieve_relevant_memories(
    query: str,
    memory_dirs: list[Path],
    *,
    max_tokens: int,
) -> RetrievalResult:
    """Retrieve relevant memory files within a token budget."""
    query_terms = [term for term in query.lower().split() if term]
    scored: list[RetrievedMemory] = []

    for path in _scan_memory_files(memory_dirs):
        try:
            parsed = parse_memory_file(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        score = _score_memory(query_terms, parsed)
        if score > 0:
            scored.append(RetrievedMemory(path=path, parsed=parsed, score=score))

    scored.sort(key=lambda memory: (-memory.score, memory.path.name))

    kept: list[RetrievedMemory] = []
    token_count = 0
    for memory in scored:
        memory_tokens = estimate_text_tokens(memory.parsed.body)
        if kept and token_count + memory_tokens > max_tokens:
            continue
        if not kept and memory_tokens > max_tokens:
            truncated_body = memory.parsed.body[: max(1, max_tokens)]
            truncated = RetrievedMemory(
                path=memory.path,
                parsed=ParsedMemoryFile(
                    memory_type=memory.parsed.memory_type,
                    description=memory.parsed.description,
                    metadata=memory.parsed.metadata,
                    body=truncated_body,
                ),
                score=memory.score,
            )
            kept.append(truncated)
            token_count = estimate_text_tokens(truncated_body)
            break
        kept.append(memory)
        token_count += memory_tokens

    return RetrievalResult(memories=kept, token_count=token_count)


async def llm_retrieve_relevant_memories(
    query: str,
    memory_dirs: list[Path],
    *,
    max_tokens: int,
    max_files: int = 5,
) -> RetrievalResult:
    """
    Retrieve relevant memory files using LLM-based selection.

    Falls back to keyword-based retrieval on any error.

    Args:
        query: User query to match against
        memory_dirs: List of directories to scan for memory files
        max_tokens: Maximum token budget for returned memories
        max_files: Maximum number of files to select (default: 5)

    Returns:
        RetrievalResult with selected memories
    """
    try:
        # Lazy import to avoid circular dependencies
        from koder_agent.utils.client import llm_completion

        # Scan all memory files
        files = _scan_memory_files(memory_dirs)
        if not files:
            return RetrievalResult(memories=[], token_count=0)

        # Build manifest: filename -> (Path, preview)
        file_map: dict[str, Path] = {}
        manifest_lines = []

        for path in files:
            try:
                content = path.read_text(encoding="utf-8")
                # Read first 30 lines as preview
                lines = content.split("\n")[:30]
                preview = "\n".join(lines)
                file_map[path.name] = path
                manifest_lines.append(f"**{path.name}**:\n{preview}\n")
            except Exception:
                continue

        if not file_map:
            return RetrievalResult(memories=[], token_count=0)

        # Build LLM prompt
        manifest_text = "\n".join(manifest_lines)
        system_message = {
            "role": "system",
            "content": (
                "You are a memory retrieval assistant. Given a user query and a list of memory files "
                "with previews, select the most relevant files. Return ONLY the filenames, one per line. "
                "If no files are relevant, return NONE. Do not include explanations or markdown."
            ),
        }
        user_message = {
            "role": "user",
            "content": (
                f"Query: {query}\n\n"
                f"Select up to {max_files} most relevant files from:\n\n{manifest_text}\n\n"
                f"Return filenames only, one per line:"
            ),
        }

        # Call LLM
        response = await llm_completion([system_message, user_message])

        # Parse response: split by newlines, filter NONE, strip whitespace
        selected_filenames = [
            line.strip()
            for line in response.strip().split("\n")
            if line.strip() and line.strip().upper() != "NONE"
        ]

        # Retrieve and parse selected files
        kept: list[RetrievedMemory] = []
        token_count = 0

        for filename in selected_filenames[:max_files]:
            if filename not in file_map:
                continue

            path = file_map[filename]
            try:
                content = path.read_text(encoding="utf-8")
                parsed = parse_memory_file(content)
                memory_tokens = estimate_text_tokens(parsed.body)

                # Check token budget
                if kept and token_count + memory_tokens > max_tokens:
                    continue

                # If first file exceeds budget, truncate it
                if not kept and memory_tokens > max_tokens:
                    truncated_body = parsed.body[: max(1, max_tokens)]
                    truncated = RetrievedMemory(
                        path=path,
                        parsed=ParsedMemoryFile(
                            memory_type=parsed.memory_type,
                            description=parsed.description,
                            metadata=parsed.metadata,
                            body=truncated_body,
                        ),
                        score=0,  # LLM-based selection doesn't use scores
                    )
                    kept.append(truncated)
                    token_count = estimate_text_tokens(truncated_body)
                    break

                kept.append(RetrievedMemory(path=path, parsed=parsed, score=0))
                token_count += memory_tokens

            except Exception:
                continue

        return RetrievalResult(memories=kept, token_count=token_count)

    except Exception:
        # Fall back to keyword-based retrieval on any error
        return retrieve_relevant_memories(query, memory_dirs, max_tokens=max_tokens)
