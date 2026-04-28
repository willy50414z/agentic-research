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


# ── Main logic (injectable for testing) ──────────────────────────────────────

def _main_logic(
    card_id: str,
    target_columns: set[str],
    timeout: int,
    interval_early: int,
    interval_late: int,
    early_window: int,
    log_source: str,
    log_grep: str,
    log_output: str,
    planka_url: str,
    token: str,
    board_id: str,
    _get_column_fn=None,
    _sleep_fn=None,
    _time_fn=None,
) -> dict:
    """
    Core polling loop. Dependencies injectable for unit tests.
    Returns dict: {status, column, elapsed_seconds, log_lines, error}
    """
    _get_col = _get_column_fn or (lambda cid, url, tok, bid: get_card_column(cid, url, tok, bid))
    _sleep   = _sleep_fn or time.sleep
    _now     = _time_fn  or time.time

    start = _now()

    while True:
        elapsed = _now() - start
        column  = _get_col(card_id, planka_url, token, board_id)

        if column in target_columns:
            log_lines = capture_logs(log_source, log_grep, log_output)
            return {
                "status":          "reached",
                "column":          column,
                "elapsed_seconds": int(elapsed),
                "log_lines":       log_lines,
                "error":           None,
            }

        if elapsed >= timeout:
            log_lines = capture_logs(log_source, log_grep, log_output)
            return {
                "status":          "timeout",
                "column":          column,
                "elapsed_seconds": int(elapsed),
                "log_lines":       log_lines,
                "error":           f"Timeout after {timeout}s. Last column: {column}",
            }

        interval = interval_early if elapsed < early_window else interval_late
        _sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Poll Planka card until target column is reached.")
    parser.add_argument("--card-id",        required=True)
    parser.add_argument("--target-columns", required=True,
                        help="Comma-separated column names, e.g. 'Verify,Planning'")
    parser.add_argument("--timeout",        type=int, default=900)
    parser.add_argument("--interval-early", type=int, default=30)
    parser.add_argument("--interval-late",  type=int, default=120)
    parser.add_argument("--early-window",   type=int, default=300)
    parser.add_argument("--log-source",     default="")
    parser.add_argument("--log-grep",       default=".")
    parser.add_argument("--log-output",     default="")
    args = parser.parse_args()

    planka_url = os.getenv("PLANKA_API_URL", "").rstrip("/")
    token      = os.getenv("PLANKA_TOKEN", "")
    board_id   = os.getenv("PLANKA_BOARD_ID", "")

    result = _main_logic(
        card_id        = args.card_id,
        target_columns = {c.strip() for c in args.target_columns.split(",")},
        timeout        = args.timeout,
        interval_early = args.interval_early,
        interval_late  = args.interval_late,
        early_window   = args.early_window,
        log_source     = args.log_source,
        log_grep       = args.log_grep,
        log_output     = args.log_output,
        planka_url     = planka_url,
        token          = token,
        board_id       = board_id,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
