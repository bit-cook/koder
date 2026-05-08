"""Tests for AskUserQuestion tool."""

import json

from koder_agent.tools.ask_user import _set_input_handler, ask_user_question


def test_single_question_single_select():
    _set_input_handler(lambda _prompt: "1")
    result = json.loads(
        ask_user_question(
            questions=json.dumps(
                [
                    {
                        "question": "Which framework?",
                        "header": "Framework",
                        "options": [
                            {"label": "React", "description": "Component-based UI"},
                            {"label": "Vue", "description": "Progressive framework"},
                        ],
                    }
                ]
            )
        )
    )
    assert result["answers"]["Which framework?"] == "React"
    _set_input_handler(None)


def test_single_question_second_option():
    _set_input_handler(lambda _prompt: "2")
    result = json.loads(
        ask_user_question(
            questions=json.dumps(
                [
                    {
                        "question": "Which DB?",
                        "header": "DB",
                        "options": [
                            {"label": "PostgreSQL", "description": "Relational"},
                            {"label": "MongoDB", "description": "Document"},
                        ],
                    }
                ]
            )
        )
    )
    assert result["answers"]["Which DB?"] == "MongoDB"
    _set_input_handler(None)


def test_multiple_questions():
    answers = iter(["1", "2"])
    _set_input_handler(lambda _prompt: next(answers))
    result = json.loads(
        ask_user_question(
            questions=json.dumps(
                [
                    {
                        "question": "Color?",
                        "header": "Color",
                        "options": [
                            {"label": "Red", "description": "Warm"},
                            {"label": "Blue", "description": "Cool"},
                        ],
                    },
                    {
                        "question": "Size?",
                        "header": "Size",
                        "options": [
                            {"label": "Small", "description": "Compact"},
                            {"label": "Large", "description": "Spacious"},
                        ],
                    },
                ]
            )
        )
    )
    assert result["answers"]["Color?"] == "Red"
    assert result["answers"]["Size?"] == "Large"
    _set_input_handler(None)


def test_invalid_input_reprompts():
    attempts = iter(["x", "0", "99", "1"])
    _set_input_handler(lambda _prompt: next(attempts))
    result = json.loads(
        ask_user_question(
            questions=json.dumps(
                [
                    {
                        "question": "Pick?",
                        "header": "Pick",
                        "options": [
                            {"label": "A", "description": "a"},
                            {"label": "B", "description": "b"},
                        ],
                    }
                ]
            )
        )
    )
    assert result["answers"]["Pick?"] == "A"
    _set_input_handler(None)


def test_validation_too_many_questions():
    _set_input_handler(lambda _prompt: "1")
    questions = [
        {
            "question": f"Q{i}?",
            "header": f"H{i}",
            "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}],
        }
        for i in range(5)
    ]
    result = json.loads(ask_user_question(questions=json.dumps(questions)))
    assert "error" in result
    _set_input_handler(None)


def test_validation_too_few_options():
    _set_input_handler(lambda _prompt: "1")
    result = json.loads(
        ask_user_question(
            questions=json.dumps(
                [
                    {
                        "question": "Only one?",
                        "header": "One",
                        "options": [{"label": "Only", "description": "alone"}],
                    }
                ]
            )
        )
    )
    assert "error" in result
    _set_input_handler(None)
