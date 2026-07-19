"""Memory extraction helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from koder_agent.utils.client import llm_completion

from .governance import (
    MAX_EXTRACTION_CANDIDATES,
    MAX_EXTRACTION_INPUT_BYTES,
    MAX_EXTRACTION_RESPONSE_BYTES,
    sanitized_error,
    validate_memory_payload,
    validate_skill_payload,
)

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


EXTRACTION_PROMPT = """You are a memory extraction assistant. Review this conversation and extract durable facts and reusable procedures.

Classify each memory into one of these types:
- user: User preferences, background, habits, personal context
- feedback: Corrections, critiques, lessons learned from feedback
- project: Project-specific facts, constraints, decisions, architecture
- reference: Reusable facts, examples, patterns worth remembering

Return a JSON object with two arrays: memories and skill_candidates.

Each factual memory should have:
- type: one of the four types above
- content: the memorable fact (concise, 1-2 sentences)
- description: why this is worth remembering

Each skill_candidate should describe a repeatable procedure and have:
- name: short kebab-case procedure name
- description: when the procedure is useful
- instructions: concise procedural steps

Do not put procedures, tool workflows, hooks, or automation instructions in memories.
Do not put factual preferences or project facts in skill_candidates.
Only extract durable information. Ignore small talk, acknowledgments, transient details, and secrets.

Conversation:
{conversation}

Return ONLY the JSON object, no explanation."""


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of extracting memory candidates from transcript messages."""

    memories: list[dict]
    errors: list[str]
    skill_candidates: list[dict] = field(default_factory=list)


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
            try:
                memories.append(
                    validate_memory_payload(
                        {
                            "type": "user",
                            "content": content.strip(),
                            "description": "User-provided durable context",
                        }
                    )
                )
            except ValueError:
                errors.append(f"message {index} was rejected by memory governance")

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
        recent_messages = messages[-max(1, max_turns * 2) :]

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
                conversation_parts.append(f"{role}: {str(content)[:16_000]}")

        conversation_text = "\n".join(conversation_parts)
        encoded_conversation = conversation_text.encode("utf-8")
        if len(encoded_conversation) > MAX_EXTRACTION_INPUT_BYTES:
            encoded_conversation = encoded_conversation[-MAX_EXTRACTION_INPUT_BYTES:]
            conversation_text = encoded_conversation.decode("utf-8", errors="ignore")

        user_prompt = EXTRACTION_PROMPT.format(conversation=conversation_text)

        llm_messages = [{"role": "user", "content": user_prompt}]
        response = await llm_completion(
            llm_messages,
            response_reserve=4_096,
        )

        # Strip code fences if present
        if not isinstance(response, str):
            raise ValueError("memory extraction response must be text")
        if len(response.encode("utf-8")) > MAX_EXTRACTION_RESPONSE_BYTES:
            raise ValueError("memory extraction response exceeds size limit")
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r"^```(?:json)?\s*\n", "", response)
            response = re.sub(r"\n```\s*$", "", response)

        extracted = json.loads(response)
        if isinstance(extracted, list):
            memories_raw = extracted
            skill_candidates_raw = []
        elif isinstance(extracted, dict):
            if not set(extracted).issubset({"memories", "skill_candidates"}):
                raise ValueError("memory extraction response has unknown fields")
            memories_raw = extracted.get("memories", [])
            skill_candidates_raw = extracted.get("skill_candidates", [])
        else:
            raise ValueError("memory extraction response must be an array or object")

        if not isinstance(memories_raw, list) or not isinstance(skill_candidates_raw, list):
            raise ValueError("memory extraction candidate collections must be arrays")

        governance_errors: list[str] = []
        combined = [("memory", item) for item in memories_raw] + [
            ("skill", item) for item in skill_candidates_raw
        ]
        if len(combined) > MAX_EXTRACTION_CANDIDATES:
            combined = combined[:MAX_EXTRACTION_CANDIDATES]
            governance_errors.append("candidate_limit: extraction candidates were truncated")

        valid_memories = []
        valid_skill_candidates = []
        for kind, candidate in combined:
            try:
                if kind == "memory":
                    valid_memories.append(validate_memory_payload(candidate))
                else:
                    valid_skill_candidates.append(validate_skill_payload(candidate))
            except ValueError:
                governance_errors.append(f"malformed_candidate: rejected {kind} candidate")

        return ExtractionResult(
            memories=valid_memories,
            errors=governance_errors,
            skill_candidates=valid_skill_candidates,
        )

    except Exception as e:
        return ExtractionResult(
            memories=[],
            errors=[sanitized_error(e)],
            skill_candidates=[],
        )
