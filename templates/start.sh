#!/bin/bash
# start.sh — Phase 1: register project + generate spec.clarified.yaml
#
# Calls framework-api /start directly (no per-project Docker container).
# Requires: agentic-research package installed, infra stack running.

set -euo pipefail

FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"

echo "[start.sh] Posting spec.yaml to framework-api..."

agentic-research start \
  --spec "$(pwd)/spec.yaml" \
  --out  "$(pwd)/spec.clarified.yaml" \
  --api  "$FRAMEWORK_API_URL"
