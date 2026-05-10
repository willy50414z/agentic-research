# Migration — revise-pipeline-checklist-audit

本文件描述 `revise-pipeline-checklist-audit` change 的部署順序、feature flag 切換策略與 rollback 路徑。
此 change 為 BREAKING（重設計 revise 流程），但透過環境變數 `REVISE_PIPELINE_VERSION` 提供漸進式 rollout 機制：v2 程式碼上線時預設仍走 v1，逐步切換以降低風險。

---

## 部署前提

部署前確認：

- 所有目前進行中（in-flight）的 project 在 v1 流程下正常運作；本 change 預設行為與 v1 一致，不應影響進行中專案
- DB schema 無變更（`projects.config.revise_pipeline_version` 為 JSON 欄位內的新 key，不需 migration script）
- Planka custom field schema 無變更
- `app/prompts/freqtrade/revise.txt` 與 `revise_validate.txt` 須保留（v1 fallback path 仍依賴）

---

## Stage 1 — 部署 v2 程式碼，flag 預設 `v1`（無行為變更）

**目標**：v2 模組與 prompt 上線但不啟用，先驗證部署本身不破壞 v1 行為。

操作：

1. Merge 本 change 對應 PR（含 `app/freqtrade/audit.py`、`app/freqtrade/checklist.py`、`app/freqtrade/strategy_extractor.py`、新 prompt 檔等）
2. 確認 `REVISE_PIPELINE_VERSION` 環境變數**未設定**或**設為 `v1`**
3. 部署到 production

驗證：

- 既有 project 觸發 revise 時走 v1 流程
- log 中可見 `REVISE_PIPELINE_VERSION=v1 (default)` 訊息
- 無新 artifacts（`intent.md` / `checklist.yaml` / `.staging/`）出現在新建 project 的 work_dir
- 既有 project 的 backtest 行為與部署前完全一致

**通過條件**：1～2 個 production project 完整跑完一輪 revise 迴圈，行為與部署前一致。

---

## Stage 2 — 在測試 project 啟用 v2，驗證核心驗收條件

**目標**：在隔離環境跑完整 v2 迴圈，驗證「v0/v1 backtest metrics 不同」這個核心驗收條件（對應 `tasks.md §10.5`）。

操作：

1. 在測試環境設 `REVISE_PIPELINE_VERSION=v2`
2. 建立測試 project，`max_loops=2`
3. 觸發完整研究迴圈（plan → implement → analyze → revise → implement → analyze）
4. 跑完後檢查 artifacts

驗證點（**全部必須通過才能進入 Stage 3**）：

- `artifacts/strategies/v0/{Strategy}.py` 與 `artifacts/strategies/v1/{Strategy}.py` 為 byte-different（`sha256` 比對）
- v0 與 v1 backtest 至少一項 metric（win_rate / profit_factor / max_drawdown）數值不同
- Planka 卡片附件含 `v1_audit.md`、`v1_revised_direction.md`、`v0_strategy_spec.md`、`v1_strategy_spec.md`
- `artifacts/.staging/v1/` 目錄存在且含 `candidate.py`（即使 promote 完成也保留）
- DB `projects.config.revise_pipeline_version = "v2"` 已寫入
- 無 `SUBAGENT_DISHONEST` 高頻發生
- 無 promote 半成品（`artifacts/strategies/v1/` 要嘛完整存在要嘛不存在）

任一驗證失敗 → 不進入 Stage 3，回到開發環境修正後重跑 Stage 2。

---

## Stage 3 — 在 1～2 個 production project 啟用 v2 觀察

**目標**：擴大樣本到真實 production 工作流，確認 v2 在多樣 spec 與 LLM 回應下穩定。

操作：

1. 選 1～2 個非關鍵 production project，將其 `REVISE_PIPELINE_VERSION` 顯式設為 `v2`（或全環境切 `v2`）
2. 觀察至少 1 週或至少 5 輪完整 revise

監控指標：

- `SUBAGENT_DISHONEST` 觸發頻率（期望：偶發；高頻表示 prompt 設計有問題）
- `unauthorized_change` 觸發頻率（期望：少數；高頻表示 LLM 整檔重寫副作用過大）
- promote 半成功事件（不應發生；任一發生即 rollback）
- audit retry 平均次數（期望 ≤ 1，超過代表 deterministic check 過嚴）
- 整體 revise 完成時間（v2 因多階段，預期比 v1 慢 1.5～2 倍，但不應超過 3 倍）

**通過條件**：

- 0 個 promote 半成功事件
- `SUBAGENT_DISHONEST` 發生率 < 5% revise 輪數
- v0/v1/v2 backtest metrics 在所有觀察 project 中可見明顯差異

任一驗證失敗 → rollback 該 project 至 v1（見下方 Rollback 章節），蒐集證據後修正再進 Stage 3。

---

## Stage 4 — 將預設值切換為 `v2`

**目標**：v2 成為新 project 的預設行為。

操作：

1. 修改 code 中 `REVISE_PIPELINE_VERSION` 預設值由 `v1` → `v2`
2. 同步更新 `docs/AGENTIC_RESEARCH_SOP_ZH.md` 與相關文件
3. 部署

影響：

- 新建 project 在未顯式設定環境變數時走 v2
- 已執行中的 project 仍按其 `projects.config.revise_pipeline_version` 跑完（不受預設值切換影響）
- 既有完成的 project 歷史 artifacts 不變動

**保留 v1 程式碼路徑至少一個 release**，以便緊急 rollback。

---

## Stage 5 — 移除 v1 程式碼與舊 prompts（cleanup）

**目標**：完成 migration，移除技術債。

前提：Stage 4 後至少觀察一個完整 release cycle，期間無重大 regression。

操作：

1. 刪除 `app/freqtrade/revise/v1.py`（或對應 v1 dispatcher 邏輯）
2. 刪除 `app/prompts/freqtrade/revise.txt`
3. 刪除 `app/prompts/freqtrade/revise_validate.txt`
4. 移除 `REVISE_PIPELINE_VERSION` 環境變數讀取邏輯（若還有 fallback 分支）
5. 保留 `projects.config.revise_pipeline_version` 欄位（歷史紀錄用）
6. Archive 此 change：`openspec archive revise-pipeline-checklist-audit`

完成後：v2 為唯一流程，無 fallback。

---

## Rollback 策略

**情境 1：Stage 1～3 觀察期發現 v2 有問題**

操作：

1. 將環境變數 `REVISE_PIPELINE_VERSION` 設為 `v1`（或 unset，預設仍是 `v1`）
2. 重新部署
3. 新建 project 與已記錄為 `v1` 的 project 自動走 v1 流程

注意：

- 已記錄為 `v2` 的 project 不會自動切回 v1（DB 內 `projects.config.revise_pipeline_version = "v2"` 為準），這些 project 後續輪次仍走 v2
- 若需要強制 rollback 特定 project，手動更新 DB：
  ```sql
  UPDATE projects
     SET config = jsonb_set(config, '{revise_pipeline_version}', '"v1"')
   WHERE id = '<project_id>';
  ```
  並確認該 project 已不依賴 v2-only artifacts（否則新 v1 輪次會找不到對應 input）

**情境 2：Stage 4 切換預設後發現 regression**

操作：

1. 緊急 hotfix：將預設值改回 `v1`、重新部署
2. 已寫入 DB 為 `v2` 的 project 仍維持 v2（如情境 1 處理）
3. 蒐集 regression 證據、修正後重新進 Stage 4

**情境 3：Stage 5 cleanup 後發現需要 v1**

此情境風險最高，因 v1 程式碼已刪除。緩解：

- Stage 5 進入前須在 Stage 4 觀察至少一個 release cycle
- Cleanup commit 須單獨成 PR，方便 revert
- 若必須 rollback，revert cleanup commit 即可恢復 v1 程式碼路徑；DB 紀錄不變

---

## 驗證 checklist 對照

各 Stage 對應 `tasks.md` 的驗證任務：

| Stage | 對應 tasks.md 任務 |
|---|---|
| Stage 1 | §10.1～§10.4 部署與 flag 機制驗證 |
| Stage 2 | §10.5 max_loops=2 v0/v1 metrics 不同驗證 |
| Stage 3 | §10.6 production 觀察 |
| Stage 4 | §10.7 預設值切換 |
| Stage 5 | §10.8 移除 v1 程式碼與 archive change |

每個 Stage 通過後在對應 task 標記完成，未通過不得進入下一階段。
