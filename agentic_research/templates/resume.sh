#!/bin/bash
# resume.sh — post clarification answers to start the research loop.
#
# Run after filling in spec.clarified.md answers.
# Calls framework-api /resume directly.
# Requires: agentic-research package installed, infra stack running.

set -euo pipefail

FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"

echo "[resume.sh] Posting clarification answers..."

agentic-research resume \
  --spec-clarified "$(pwd)/spec.clarified.md" \
  --spec           "$(pwd)/spec.md" \
  --api            "$FRAMEWORK_API_URL"
