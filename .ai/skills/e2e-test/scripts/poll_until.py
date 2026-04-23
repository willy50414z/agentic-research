"""poll_until.py — Adaptive Planka card column poller with log capture.

Usage:
    python poll_until.py \
        --card-id <id> \
        --target-columns "Verify,Planning" \
        --timeout 900 \
        --interval-early 30 \
        --interval-late 120 \
        --early-window 300 \
        --log-source "docker:agentic-framework-api" \
        --log-grep "SPEC.REVIEW" \
        --log-output /path/to/logs/spec-review.log

Outputs JSON to stdout:
    {"status": "reached", "column": "Verify", "elapsed_seconds": 312, "log_lines": 45, "error": null}
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx


# ── Planka API helpers ────────────────────────────────────────────────────────

def get_card_column(card_id: str, planka_url: str, token: str, board_id: str) -> str | None:
    """GET card → listId → board lists → column name. Returns None on any error."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        card_resp = httpx.get(f"{planka_url}/api/cards/{card_id}", headers=headers, timeout=10)
        card_resp.raise_for_status()
        list_id = card_resp.json()["item"]["listId"]

        board_resp = httpx.get(f"{planka_url}/api/boards/{board_id}", headers=headers, timeout=10)
        board_resp.raise_for_status()
        lists = board_resp.json().get("included", {}).get("lists", [])
        for lst in lists:
            if lst.get("id") == list_id:
                return lst.get("name")
    except Exception as e:
        print(f"[poll_until] get_card_column error: {e}", file=sys.stderr)
    return None


# ── Log capture ───────────────────────────────────────────────────────────────

def capture_logs(log_source: str, grep_pattern: str, output_path: str) -> int:
    """Capture and filter logs. Returns number of matching lines saved."""
    if not log_source:
        return 0

    lines: list[str] = []
    try:
        if log_source.startswith("file:"):
            path = Path(log_source[5:])
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                lines = [l for l in content.splitlines() if re.search(grep_pattern, l)]
        elif log_source.startswith("docker:"):
            container = log_source[7:]
            proc = subprocess.run(
                ["docker", "logs", container, "--tail", "500"],
                capture_output=True, text=True, timeout=15,
            )
            all_lines = proc.stdout.splitlines() + proc.stderr.splitlines()
            lines = [l for l in all_lines if re.search(grep_pattern, l)]
    except Exception as e:
        print(f"[poll_until] log capture error: {e}", file=sys.stderr)
        return 0

    if lines and output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(lines[-500:]), encoding="utf-8")

    return len(lines)


# ── Placeholder for main() — implemented in Task 5 ───────────────────────────

def main():
    raise NotImplementedError("main() implemented in Task 5")


if __name__ == "__main__":
    main()
