"""setup_card.py — 建立 Planka 測試卡片，上傳 spec.md，移至 Spec Pending Review。

Usage:
    python setup_card.py \
        --spec-path tests/README.md \
        --run-id 20260423-143000

Outputs JSON to stdout:
    {"card_id": "xxx", "thread_id": "e2e-test-143000", "error": null}

環境變數（從 .env 讀取）：
    PLANKA_API_URL, PLANKA_TOKEN, PLANKA_BOARD_ID
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx


def setup_card(
    planka_url: str,
    token: str,
    board_id: str,
    spec_path: str,
    run_id: str,
) -> dict:
    """
    建立測試卡片、上傳 spec.md、移至 Spec Pending Review。
    回傳 {"card_id": str, "thread_id": str, "error": str | None}。
    """
    headers = {"Authorization": f"Bearer {token}"}
    thread_id = f"e2e-test-{run_id.split('-')[-1]}"  # e.g. e2e-test-143000

    # ── 驗證 spec 檔案存在 ──────────────────────────────────────────────────
    spec = Path(spec_path)
    if not spec.exists():
        return {"card_id": None, "thread_id": thread_id,
                "error": f"spec file not found: {spec_path}"}

    try:
        # ── 取得 Planning + Spec Pending Review 的 list_id ─────────────────
        board_resp = httpx.get(
            f"{planka_url}/api/boards/{board_id}", headers=headers, timeout=10
        )
        board_resp.raise_for_status()
        lists = board_resp.json().get("included", {}).get("lists", [])
        list_map = {lst["name"]: lst["id"] for lst in lists}

        planning_id = list_map.get("Planning")
        spr_id      = list_map.get("Spec Pending Review")
        if not planning_id or not spr_id:
            return {"card_id": None, "thread_id": thread_id,
                    "error": f"Required columns not found. Available: {list(list_map.keys())}"}

        # ── 建立卡片（在 Planning）──────────────────────────────────────────
        card_resp = httpx.post(
            f"{planka_url}/api/lists/{planning_id}/cards",
            headers=headers,
            json={"name": f"[E2E Test] Turtle Trading {run_id}", "position": 65535},
            timeout=10,
        )
        card_resp.raise_for_status()
        card_id = card_resp.json()["item"]["id"]

        # ── 注入 thread_id 到 description ─────────────────────────────────
        httpx.patch(
            f"{planka_url}/api/cards/{card_id}",
            headers=headers,
            json={"description": f"thread_id: {thread_id}\n\n[E2E automated test run {run_id}]"},
            timeout=10,
        ).raise_for_status()

        # ── 上傳 spec.md 附件 ───────────────────────────────────────────────
        spec_content = spec.read_bytes()
        attach_resp = httpx.post(
            f"{planka_url}/api/cards/{card_id}/attachments",
            headers=headers,
            files={"file": ("spec.md", spec_content, "text/markdown")},
            timeout=30,
        )
        attach_resp.raise_for_status()

        # ── 移動卡片至 Spec Pending Review（觸發 webhook）────────────────
        httpx.patch(
            f"{planka_url}/api/cards/{card_id}",
            headers=headers,
            json={"listId": spr_id, "position": 65535},
            timeout=10,
        ).raise_for_status()

        return {"card_id": card_id, "thread_id": thread_id, "error": None}

    except Exception as e:
        return {"card_id": None, "thread_id": thread_id, "error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-path", required=True)
    parser.add_argument("--run-id",    required=True)
    args = parser.parse_args()

    result = setup_card(
        planka_url = os.getenv("PLANKA_API_URL", "").rstrip("/"),
        token      = os.getenv("PLANKA_TOKEN", ""),
        board_id   = os.getenv("PLANKA_BOARD_ID", ""),
        spec_path  = args.spec_path,
        run_id     = args.run_id,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
