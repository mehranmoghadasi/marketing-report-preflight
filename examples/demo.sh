#!/usr/bin/env bash
# Run the demo client through preflight and write the artefacts committed in examples/output.
# Usage (from the repository root, after `pip install -e ".[dev]"`):
#     bash examples/demo.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
out="examples/output"
mkdir -p "$out"

# --no-db keeps the demo side-effect free; drop it (or pass --db path) to record history.
report-preflight run \
  --client northgate-plumbing \
  --period 2026-08 \
  --clients-dir examples/clients \
  --out "$out" \
  --no-db \
  --no-color

echo
echo "--- gate behaviour (what cron or CI sees) ---"
set +e
report-preflight run \
  --client northgate-plumbing \
  --period 2026-08 \
  --clients-dir examples/clients \
  --no-db \
  --exit-code >/dev/null
echo "report-preflight run --exit-code -> $?   (1 = do not send)"
