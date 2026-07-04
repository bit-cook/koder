"""AI-powered shell command classifier for auto/YOLO mode.

Uses an LLM to semantically classify shell commands as safe, moderate,
or dangerous. Falls back to conservative defaults on LLM failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum


class RiskLevel(Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class ClassificationResult:
    command: str
    risk_level: RiskLevel
    allowed: bool
    reason: str
    # True when the classifier itself failed (LLM unavailable, bad response, ...).
    # Callers must treat this as "no verdict" and fall back to static policy,
    # never as an authoritative denial of the command.
    error: bool = False


CLASSIFIER_SYSTEM_PROMPT = """You are a shell command security classifier. Classify commands into risk levels:

- "safe": Read-only commands that cannot modify the system (ls, cat, grep, git status, git log, echo, pwd, whoami, date, uname, wc, head, tail, find with no -exec/-delete, stat)
- "moderate": Commands that make contained changes (git add, git commit, git push, npm install, pip install, make, cargo build, mkdir, touch, cp within workspace)
- "dangerous": Commands that can cause data loss, system changes, or security issues (rm -rf, chmod 777, dd, mkfs, shutdown, reboot, curl|bash, wget|sh, sudo, kill -9, DROP TABLE, format, fdisk, iptables, eval, exec arbitrary code)

Rules:
- Piped commands: classify by the most dangerous segment
- Commands with redirections (>): classify as moderate (can overwrite files)
- Commands with sudo: always dangerous
- Unknown commands: classify as moderate

Return JSON: {"risk_level": "safe|moderate|dangerous", "allowed": true|false, "reason": "brief explanation"}
- safe → allowed=true
- moderate → allowed=true (but flag for review)
- dangerous → allowed=false"""


class AiShellClassifier:
    """LLM-powered shell command classifier."""

    def __init__(self):
        self.system_prompt = CLASSIFIER_SYSTEM_PROMPT

    async def classify(
        self,
        command: str,
        context: str | None = None,
    ) -> ClassificationResult:
        """Classify a shell command using the LLM.

        Falls back to moderate/deny on any error.
        """
        from koder_agent.utils.client import llm_completion

        user_content = f"Classify this shell command: {command}"
        if context:
            user_content += f"\n\nProject context: {context}"

        try:
            response = await llm_completion(
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_content},
                ]
            )

            # Strip code fences
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            data = json.loads(text)
            risk = RiskLevel(data.get("risk_level", "moderate"))
            return ClassificationResult(
                command=command,
                risk_level=risk,
                allowed=data.get("allowed", risk == RiskLevel.SAFE),
                reason=data.get("reason", ""),
            )

        except Exception:
            # Classifier failure is not a verdict on the command: surface the
            # error so callers fall back to the static approval flow.
            return ClassificationResult(
                command=command,
                risk_level=RiskLevel.MODERATE,
                allowed=False,
                reason="AI classifier unavailable, defaulting to manual approval",
                error=True,
            )

    def classify_sync(
        self,
        command: str,
        context: str | None = None,
    ) -> ClassificationResult:
        """Synchronous wrapper for classify() using asyncio.run().

        Falls back to moderate/deny on any error.
        """
        import asyncio

        try:
            # Check if we're already in an event loop
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run()
                return asyncio.run(self.classify(command, context))

            # Already in event loop, can't use asyncio.run()
            # Fall back to conservative default
            return ClassificationResult(
                command=command,
                risk_level=RiskLevel.MODERATE,
                allowed=False,
                reason="AI classifier unavailable in sync context (already in event loop)",
                error=True,
            )
        except Exception:
            # Conservative fallback
            return ClassificationResult(
                command=command,
                risk_level=RiskLevel.MODERATE,
                allowed=False,
                reason="AI classifier unavailable, defaulting to manual approval",
                error=True,
            )
