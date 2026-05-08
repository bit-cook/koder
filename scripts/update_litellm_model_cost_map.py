#!/usr/bin/env python3
"""Update Koder's vendored LiteLLM model cost map."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "koder_agent" / "data" / "model_prices_and_context_window.json"
DEFAULT_MIN_MODEL_COUNT = 1000


def _fetch_model_cost_map(source_url: str, *, timeout: float) -> Any:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "koder-litellm-model-cost-map-updater"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw_content = response.read().decode("utf-8")
    return json.loads(raw_content)


def _validate_model_cost_map(content: Any, *, min_model_count: int) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ValueError("model cost map must be a JSON object")
    if len(content) < min_model_count:
        raise ValueError(
            f"model cost map has {len(content)} entries, expected at least {min_model_count}"
        )

    has_model_metadata = any(
        isinstance(metadata, dict)
        and (
            "max_input_tokens" in metadata
            or "input_cost_per_token" in metadata
            or "output_cost_per_token" in metadata
        )
        for metadata in content.values()
    )
    if not has_model_metadata:
        raise ValueError("model cost map does not contain model metadata entries")

    return content


def _write_model_cost_map(content: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    output_path.write_text(rendered, encoding="utf-8")


def update_model_cost_map(
    *,
    source_url: str = DEFAULT_SOURCE_URL,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    timeout: float = 30.0,
    min_model_count: int = DEFAULT_MIN_MODEL_COUNT,
    dry_run: bool = False,
) -> dict[str, Any]:
    content = _fetch_model_cost_map(source_url, timeout=timeout)
    content = _validate_model_cost_map(content, min_model_count=min_model_count)
    if not dry_run:
        _write_model_cost_map(content, output_path)
    return content


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh koder_agent/data/model_prices_and_context_window.json from LiteLLM."
    )
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="JSON source URL")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output path for the vendored JSON file",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Download timeout in seconds")
    parser.add_argument(
        "--min-model-count",
        type=int,
        default=DEFAULT_MIN_MODEL_COUNT,
        help="Reject maps with fewer entries than this count",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing the file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        content = update_model_cost_map(
            source_url=args.source_url,
            output_path=args.output,
            timeout=args.timeout,
            min_model_count=args.min_model_count,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"failed to update LiteLLM model cost map: {exc}", file=sys.stderr)
        return 1

    action = "validated" if args.dry_run else "updated"
    print(f"{action} {args.output} with {len(content)} model entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
