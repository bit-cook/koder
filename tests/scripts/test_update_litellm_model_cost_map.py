from __future__ import annotations

import json

import pytest

from scripts.update_litellm_model_cost_map import (
    _validate_model_cost_map,
    update_model_cost_map,
)


def test_validate_model_cost_map_accepts_metadata_entries():
    content = {
        "model-a": {"max_input_tokens": 1000},
        "model-b": {"input_cost_per_token": 0.1, "output_cost_per_token": 0.2},
    }

    assert _validate_model_cost_map(content, min_model_count=2) == content


def test_validate_model_cost_map_rejects_too_few_entries():
    with pytest.raises(ValueError, match="expected at least 3"):
        _validate_model_cost_map({"model-a": {"max_input_tokens": 1000}}, min_model_count=3)


def test_update_model_cost_map_from_file_url(tmp_path):
    source = tmp_path / "source.json"
    output = tmp_path / "vendored.json"
    source.write_text(
        json.dumps(
            {
                "model-b": {"input_cost_per_token": 0.1},
                "model-a": {"max_input_tokens": 1000},
            }
        ),
        encoding="utf-8",
    )

    content = update_model_cost_map(
        source_url=source.as_uri(),
        output_path=output,
        min_model_count=2,
    )

    assert content["model-a"]["max_input_tokens"] == 1000
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == content
