"""AskUserQuestion tool — CLI numbered-option prompts."""

from __future__ import annotations

import json
import sys
from typing import Callable

from .compat import function_tool

_input_handler: Callable[[str], str] | None = None


def _set_input_handler(handler: Callable[[str], str] | None) -> None:
    global _input_handler
    _input_handler = handler


def _get_input(prompt: str) -> str:
    if _input_handler is not None:
        return _input_handler(prompt)
    return input(prompt)


_MAX_QUESTIONS = 4
_MIN_OPTIONS = 2
_MAX_OPTIONS = 4


def _validate_questions(questions: list[dict]) -> str | None:
    if len(questions) < 1:
        return "At least 1 question required"
    if len(questions) > _MAX_QUESTIONS:
        return f"At most {_MAX_QUESTIONS} questions allowed, got {len(questions)}"
    seen_q: set[str] = set()
    for i, q in enumerate(questions):
        text = q.get("question", "")
        if not text:
            return f"Question {i + 1} is missing 'question' text"
        if text in seen_q:
            return f"Duplicate question text: {text}"
        seen_q.add(text)
        options = q.get("options", [])
        if len(options) < _MIN_OPTIONS:
            return (
                f"Question '{text}': at least {_MIN_OPTIONS} options required, got {len(options)}"
            )
        if len(options) > _MAX_OPTIONS:
            return f"Question '{text}': at most {_MAX_OPTIONS} options allowed, got {len(options)}"
        seen_labels: set[str] = set()
        for opt in options:
            label = opt.get("label", "")
            if label in seen_labels:
                return f"Question '{text}': duplicate option label '{label}'"
            seen_labels.add(label)
    return None


def _ask_one_question(q: dict) -> str:
    options = q["options"]
    header = q.get("header", "")
    print(f"\n{'─' * 40}", file=sys.stderr)
    if header:
        print(f"[{header}]", file=sys.stderr)
    print(f"{q['question']}\n", file=sys.stderr)
    for i, opt in enumerate(options, 1):
        desc = opt.get("description", "")
        print(f"  {i}. {opt['label']}", file=sys.stderr)
        if desc:
            print(f"     {desc}", file=sys.stderr)
    print(file=sys.stderr)

    max_attempts = 10
    for _ in range(max_attempts):
        raw = _get_input(f"Select (1-{len(options)}): ").strip()
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return options[choice - 1]["label"]
        except (ValueError, IndexError):
            pass
        print(f"Please enter a number between 1 and {len(options)}.", file=sys.stderr)
    return options[0]["label"]


def ask_user_question(questions: str) -> str:
    """Ask multiple-choice questions to gather requirements or clarify ambiguity.

    Args:
        questions: JSON array of question objects. Each has: question (str),
            header (str), options (array of {label, description}).
            Min 1, max 4 questions. Each question needs 2-4 options.
    """
    try:
        parsed = json.loads(questions)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "Invalid JSON in questions parameter"})

    if not isinstance(parsed, list):
        return json.dumps({"error": "questions must be a JSON array"})

    error = _validate_questions(parsed)
    if error:
        return json.dumps({"error": error})

    answers: dict[str, str] = {}
    for q in parsed:
        label = _ask_one_question(q)
        answers[q["question"]] = label

    return json.dumps({"questions": parsed, "answers": answers})


ask_user_question_tool = function_tool(ask_user_question)
