#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../.."

uv run scripts/tmux_feature_scenarios.py --check
uv run scripts/tmux_feature_scenarios.py --run-all
