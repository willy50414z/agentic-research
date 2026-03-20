#!/bin/bash
# start.sh — spec review: moves card to Spec Pending Review, starts graph if clean.
#
# Calls framework-api /start directly.
# Requires: agentic-research package installed, infra stack running.

set -euo pipefail

FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"

echo "[start.sh] Submitting spec.md to framework-api..."

agentic-research start \
  --spec "$(pwd)/spec.md" \
  --out  "$(pwd)/spec.clarified.md" \
  --api  "$FRAMEWORK_API_URL"
