#!/usr/bin/env python3
"""Run no-model smoke checks against Koder sandbox backends."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from koder_agent.harness.sandbox.registry import BACKEND_IDS, get_backend_status  # noqa: E402
from koder_agent.harness.sandbox.smoke import run_backend_smoke  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="append", help="Backend id to test")
    parser.add_argument("--all", action="store_true", help="Check every registered backend")
    parser.add_argument(
        "--case",
        default="default",
        choices=("default", "escape", "protected-paths", "network-deny"),
        help="Smoke case to run",
    )
    parser.add_argument(
        "--skip-unavailable",
        action="store_true",
        help="Exit successfully when a selected backend is unavailable",
    )
    parser.add_argument(
        "--skip-unconfigured",
        action="store_true",
        help="Exit successfully when a selected backend is missing credentials",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    backends = list(BACKEND_IDS if args.all else args.backend or [])
    if not backends:
        print("Provide --backend <id> or --all", file=sys.stderr)
        return 2

    failed = False
    for backend_id in backends:
        status = get_backend_status(backend_id, selected=True)
        skip = args.skip_unavailable or (
            args.skip_unconfigured
            and backend_id != "unix-local"
            and bool(status.credential_errors or status.dependency_errors)
        )
        result = run_backend_smoke(
            backend_id,
            case=args.case,
            skip_unavailable=skip,
        )
        checks = ",".join(result.checks) if result.checks else "none"
        if result.skipped:
            print(f"{backend_id}: skipped reason={result.reason} checks={checks}")
            continue
        if result.passed:
            print(f"{backend_id}: passed checks={checks}")
            continue
        failed = True
        print(f"{backend_id}: failed reason={result.reason} checks={checks}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
