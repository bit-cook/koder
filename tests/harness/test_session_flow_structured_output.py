from __future__ import annotations

import json

import pytest

from koder_agent.harness.session_flow import (
    _augment_prompt_for_json_schema,
    _extract_structured_output,
    _load_json_schema,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer"},
        "label": {"type": "string"},
    },
    "required": ["answer", "label"],
    "additionalProperties": False,
}


def test_load_json_schema_accepts_inline_object():
    loaded = _load_json_schema(json.dumps(SCHEMA))

    assert loaded == SCHEMA


def test_load_json_schema_accepts_file(tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")

    loaded = _load_json_schema(str(schema_path))

    assert loaded == SCHEMA


def test_augment_prompt_for_json_schema_requests_exact_json():
    prompt = _augment_prompt_for_json_schema("Return the answer", SCHEMA)

    assert "Return the answer" in prompt
    assert "Return only valid JSON" in prompt
    assert json.dumps(SCHEMA, ensure_ascii=False) in prompt


def test_extract_structured_output_accepts_fenced_json():
    response = 'Here is the result:\n```json\n{"answer": 42, "label": "ok"}\n```'

    parsed = _extract_structured_output(response, SCHEMA)

    assert parsed == {"answer": 42, "label": "ok"}


def test_extract_structured_output_accepts_bare_json_array_schema():
    schema = {"type": "array", "items": {"type": "string"}}

    parsed = _extract_structured_output('["alpha", "beta"]', schema)

    assert parsed == ["alpha", "beta"]


def test_extract_structured_output_rejects_schema_mismatch():
    with pytest.raises(ValueError, match="did not match"):
        _extract_structured_output('{"answer": "wrong", "label": "ok"}', SCHEMA)


def test_extract_structured_output_rejects_non_json_response():
    with pytest.raises(ValueError, match="valid JSON"):
        _extract_structured_output("plain text only", SCHEMA)
