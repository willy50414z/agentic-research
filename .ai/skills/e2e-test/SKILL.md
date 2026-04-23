---
name: e2e-test
description: >
  執行 agentic-research 框架端對端整合測試。從建立 Planka 卡片開始，驅動完整
  pipeline（Spec Review → Research Graph）至第一輪結束，嚴格驗證每個里程碑，
  並將全部過程記錄在 progress.md 供 review。
  觸發時機：使用者輸入 /e2e-test，或要求執行端對端測試、整合測試、pipeline 驗證時。
---

# E2E Integration Test

## 前置需求確認

從 `.env` 讀取並驗證以下變數存在（缺少任一則 abort）：
- `PLANKA_API_URL`、`PLANKA_TOKEN`、`PLANKA_BOARD_ID`
- `DATABASE_URL`
- `BACKTEST_MODE`（預設 `mock`）
- `LOG_SOURCE`（選填）

## 執行步驟

### Phase 1 — 環境確認

1. 執行 `docker compose ps` 確認 `agentic-research-postgres`、`agentic-planka`、
   `agentic-minio` 均 healthy。任一不健康則 abort。

2. 呼叫 `GET http://localhost:8002/health`：
   - 若 200 → 繼續
   - 若失敗 → 背景啟動 `python main.py`（`run_in_background=True`），
     每 5 秒 poll `/health`，最多等 30 秒，仍失敗則 abort

3. 呼叫 `GET http://localhost:8002/health/llm`，記錄 provider 狀態

4. 建立 run 目錄與 progress.md 骨架：
   ```python
   from datetime import datetime
   run_id  = datetime.now().strftime("%Y%m%d-%H%M%S")
   run_dir = f".ai/e2e-test-runs/{run_id}"
   # mkdir run_dir/logs/
   # 寫入 progress.md skeleton（見「Progress.md 範本」段落）
   ```

### Phase 2 — 建立測試卡片

5. 執行 `setup_card.py`：
   ```bash
   python .ai/skills/e2e-test/scripts/setup_card.py \
     --spec-path tests/README.md \
     --run-id {run_id}
   ```
   解析 JSON 輸出，取得 `card_id`、`thread_id`。
   `error` 非 null → abort，記錄錯誤到 progress.md。

6. 更新 progress.md Phase 2 checklist（card_id、thread_id 實際值）

### Phase 3 — Spec Review 監測

7. 記錄開始時間戳
8. 執行 `poll_until.py`（timeout 15 分鐘）：
   ```bash
   python .ai/skills/e2e-test/scripts/poll_until.py \
     --card-id {card_id} \
     --target-columns "Verify,Planning" \
     --timeout 900 \
     --interval-early 30 \
     --interval-late 60 \
     --early-window 300 \
     --log-source "{LOG_SOURCE}" \
     --log-grep "SPEC.REVIEW|spec_review|NODE ENTER.*SPEC" \
     --log-output {run_dir}/logs/spec-review.log
   ```
9. 解析 JSON 輸出，更新 progress.md Phase 3（等待時間、最終 column）

### Phase 4 — Spec Review 斷言

對每個斷言項目，更新 progress.md 為 ✅/❌/⏭ + 實際值：

10. **4-1** 確認 column 為 `Verify`（非 `Planning`）：
    ```python
    import httpx, os
    headers = {"Authorization": f"Bearer {os.getenv('PLANKA_TOKEN')}"}
    card    = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}", headers=headers).json()
    list_id = card["item"]["listId"]
    board   = httpx.get(f"{PLANKA_URL}/api/boards/{BOARD_ID}", headers=headers).json()
    column  = next((l["name"] for l in board["included"]["lists"] if l["id"] == list_id), None)
    ```

11. **4-2** 確認任一 comment 含 `[SPEC-REVIEW] PASS`：
    ```python
    actions  = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}/actions", headers=headers).json()
    comments = [a["data"]["text"] for a in actions.get("items", [])
                if a.get("type") == "commentCard"]
    pass_comment = next((c for c in comments if "[SPEC-REVIEW] PASS" in c), None)
    ```

12. **4-3** 確認任一 comment 含 `plugin: quant_alpha`

13. **4-4/4-5** 確認附件含 `reviewed_spec_initial.md` 與 `reviewed_spec_final.md`：
    ```python
    card_detail  = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}", headers=headers).json()
    attachments  = card_detail.get("included", {}).get("attachments", [])
    attach_names = [a["name"] for a in attachments]
    ```

14. **4-6** 若 `LOG_SOURCE` 已設定，確認 `spec-review.log` 含 `[NODE ENTER] SPEC_REVIEW_INIT`

### Phase 5 — Research Graph 監測

15. 確認卡片在 Verify（spec review 已把卡片移過去，webhook 應自動觸發）
16. 記錄開始時間戳
17. 執行 `poll_until.py`（adaptive，timeout 30 分鐘）：
    ```bash
    python .ai/skills/e2e-test/scripts/poll_until.py \
      --card-id {card_id} \
      --target-columns "Done,Failed,Review" \
      --timeout 1800 \
      --interval-early 30 \
      --interval-late 120 \
      --early-window 300 \
      --log-source "{LOG_SOURCE}" \
      --log-grep "NODE ENTER|NODE EXIT|ROUTE|QuantAlpha" \
      --log-output {run_dir}/logs/research.log
    ```
18. 解析輸出，更新 progress.md Phase 5

### Phase 6 — Research 斷言 + 最終報告

19. **6-1** 確認最終 column 在 `Done`/`Failed`/`Review`（記錄實際值）
20. **6-2** 確認任一 comment 含 `last_result=`（loop metrics）
21. **6-3** 確認附件名稱符合 `v*_researchsummary_*.md` pattern
22. **6-4** 若有 log，確認含 `[NODE ENTER] PLAN`、`IMPLEMENT`、`TEST`、`ANALYZE`

23. 執行 `extract_metrics.py`：
    ```bash
    python .ai/skills/e2e-test/scripts/extract_metrics.py \
      --mode {BACKTEST_MODE} \
      --artifacts-dir ./artifacts \
      --output {run_dir}/metrics_summary.json
    ```
24. 讀取 `metrics_summary.json`，格式化為 Markdown 表格，插入 progress.md

25. 計算通過率（✅ 數 / 總 checklist 數），寫入最終結果區塊

26. 輸出：
    ```
    ✅ E2E Test 完成。結果：{PASS/FAIL}（{n}/{m} 通過）
    Progress report: {run_dir}/progress.md
    ```

## Progress.md 範本

```markdown
# E2E Test Run — {YYYY-MM-DD HH:MM:SS}

## 環境
- run_id: {run_id}
- thread_id: （待填入）
- card_id: （待填入）
- BACKTEST_MODE: {mock|real}
- LOG_SOURCE: {value|未設定}
- API: http://localhost:8002
- Planka: {PLANKA_API_URL}

## Phase 1 — 前置確認
- [ ] postgres healthy
- [ ] planka healthy
- [ ] minio healthy
- [ ] API /health 200
- [ ] API /health/llm — providers: （待填）

## Phase 2 — Setup
- [ ] 卡片建立 — card_id: （待填）
- [ ] spec.md 上傳成功
- [ ] 卡片移至 Spec Pending Review

## Phase 3 — Spec Review 監測
- 等待時間: —
- 最終 column: —

## Phase 4 — Spec Review 斷言
- [ ] 4-1 卡片在 Verify column
- [ ] 4-2 [SPEC-REVIEW] PASS comment 存在
- [ ] 4-3 plugin: quant_alpha
- [ ] 4-4 附件 reviewed_spec_initial.md
- [ ] 4-5 附件 reviewed_spec_final.md
- [ ] 4-6 Log 含 SPEC_REVIEW_INIT

## Phase 5 — Research 監測
- 等待時間: —
- 最終 column: —

## Phase 6 — Research 斷言
- [ ] 6-1 最終 column 在預期範圍
- [ ] 6-2 loop metrics comment 存在
- [ ] 6-3 researchsummary 附件存在
- [ ] 6-4 Log 含關鍵節點

## Artifact 統計
（由 extract_metrics.py 填入）

## 擷取的 Log 片段
### Spec Review
（來自 logs/spec-review.log）

### Research Graph
（來自 logs/research.log）

## 最終結果
**整體判定**：（PASS / FAIL）
**通過率**：— / —
**耗時**：—
**失敗項目**：
```

## 錯誤處理原則

- Phase 1 任何步驟失敗 → 記錄原因到 progress.md，abort（不繼續）
- Phase 2-6 斷言失敗 → 記錄 ❌ + 實際值，繼續執行（不 abort）
- poll_until.py timeout → 記錄 TIMEOUT，繼續後續斷言
- API 呼叫失敗 → 記錄錯誤訊息，斷言標記 ❌
- `extract_metrics.py` 回傳 error 鍵 → 記錄警告，不影響整體 PASS/FAIL 判定
