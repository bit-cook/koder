"""Memory extraction helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from koder_agent.utils.client import llm_completion

MEMORY_TYPES = {
    "user": {
        "description": "User preferences, background, habits, and personal context",
        "when_to_save": "User shares information about themselves, their workflow, preferences, or personal context",
    },
    "feedback": {
        "description": "Corrections, critiques, or lessons learned from user feedback",
        "when_to_save": "User corrects the assistant, provides feedback, or points out mistakes",
    },
    "project": {
        "description": "Project-specific facts, constraints, decisions, and architecture",
        "when_to_save": "User shares project-specific information, architectural decisions, or constraints",
    },
    "reference": {
        "description": "Reusable facts, examples, or patterns worth remembering",
        "when_to_save": "User shares general knowledge, patterns, or examples that could be useful later",
    },
}


EXTRACTION_PROMPT = """You are a memory extraction assistant. Review this conversation and extract memorable facts.

Classify each memory into one of these types:
- user: User preferences, background, habits, personal context
- feedback: Corrections, critiques, lessons learned from feedback
- project: Project-specific facts, constraints, decisions, architecture
- reference: Reusable facts, examples, patterns worth remembering

Return a JSON array of memories. Each memory should have:
- type: one of the four types above
- content: the memorable fact (concise, 1-2 sentences)
- description: why this is worth remembering

Only extract facts that are genuinely useful to remember. Ignore small talk, acknowledgments, and transient details.

Conversation:
{conversation}

Return ONLY the JSON array, no explanation."""


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of extracting memory candidates from transcript messages."""

    memories: list[dict]
    errors: list[str]


def extract_memories_from_messages(messages: list[dict]) -> ExtractionResult:
    """Extract simple memory candidates from well-formed transcript messages."""
    memories: list[dict] = []
    errors: list[str] = []

    for index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            errors.append(f"message {index} is malformed")
            continue
        if role == "user" and content.strip():
            memories.append({"type": "user", "content": content.strip()})

    return ExtractionResult(memories=memories, errors=errors)


async def llm_extract_memories(messages: list[dict], *, max_turns: int = 5) -> ExtractionResult:
    """Extract typed memories from conversation using LLM.

    Args:
        messages: Conversation messages to extract from
        max_turns: Maximum number of recent turns to include (default 5)

    Returns:
        ExtractionResult with typed memories and any errors
    """
    try:
        # Take last 50 messages to avoid token limits
        recent_messages = messages[-50:]

        # Build conversation text, handling multimodal content
        conversation_parts = []
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle multimodal content (extract text blocks)
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content = " ".join(text_parts)

            if content:
                conversation_parts.append(f"{role}: {content}")

        conversation_text = "\n".join(conversation_parts)

        # Build prompt
        user_prompt = EXTRACTION_PROMPT.format(conversation=conversation_text)

        # Call LLM
        llm_messages = [{"role": "user", "content": user_prompt}]
        response = await llm_completion(llm_messages)

        # Strip code fences if present
        response = response.strip()
        if response.startswith("```"):
            # Remove opening fence
            response = re.sub(r"^```(?:json)?\s*\n", "", response)
            # Remove closing fence
            response = re.sub(r"\n```\s*$", "", response)

        # Parse JSON
        memories_raw = json.loads(response)

        # Filter to valid types only
        valid_memories = []
        for memory in memories_raw:
            if isinstance(memory, dict) and memory.get("type") in MEMORY_TYPES:
                valid_memories.append(memory)

        return ExtractionResult(memories=valid_memories, errors=[])

    except Exception as e:
        return ExtractionResult(memories=[], errors=[str(e)])
