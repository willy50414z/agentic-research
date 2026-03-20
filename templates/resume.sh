#!/bin/bash
# resume.sh — advance Phase 1 clarification loop or trigger Phase 2
#
# Calls framework-api /resume directly (no per-project Docker container).
# Requires: agentic-research package installed, infra stack running.

set -euo pipefail

FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"

echo "[resume.sh] Advancing workflow..."

agentic-research resume \
  --spec-clarified "$(pwd)/spec.clarified.yaml" \
  --spec           "$(pwd)/spec.yaml" \
  --api            "$FRAMEWORK_API_URL"
