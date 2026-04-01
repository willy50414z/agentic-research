# LangGraph 在系統中的運作流程說明

本系統（Agentic Research）採用 **LangGraph** 作為核心的任務調度與流程控制引擎。LangGraph 負責連接 LLM 代理（Agent）、工具（Tools）以及持久化存儲，實現了複雜的、具備狀態（Stateful）且可循環（Cyclic）的研究工作流。

---

## 1. 系統與 LangGraph 的關係架構

系統將任務抽象為「圖（Graph）」，其中：
- **節點（Nodes）**：執行具體任務的單元，如 LLM 調用、程式碼執行、測試運行等。
- **邊（Edges）**：定義任務的先後順序。
- **條件邊（Conditional Edges）**：根據前一個節點的輸出結果（如測試是否通過），決定下一個路徑。
- **狀態（State）**：在圖中流轉的資料對象，記錄了研究進度、規格書、測試指標等資訊。
- **檢查點（Checkpointer）**：使用 PostgreSQL 記錄每一輪的狀態，支援系統崩潰後恢復或人工干預（HITL）。

---

## 2. 核心工作流實作

系統中目前存在兩大核心 LangGraph 實作：

### A. Spec Review Graph (技術規格審查圖)
位於 `framework/spec_review_graph.py`，負責在研究開始前對技術規格（Spec）進行多輪 LLM 評審。

1.  **INIT** (`_spec_review_init`): 初始化狀態，讀取 Markdown 規格文件，設定參與審查的 LLM 列表。
2.  **ROUND (Looping)** (`_spec_review_round`): 
    - **第一輪 (Author)**: 由第一個 LLM 充當作者，強化原始規格。
    - **中間輪 (Reviewers)**: 由後續 LLM 扮演評審，針對規格書內容提出疑問。
    - **最後一輪 (Synthesizer)**: 回到第一個 LLM，彙整所有評審意見，產出最終增強版規格。
3.  **FINALIZE** (`_spec_finalize`): 
    - 若有懸而未決的問題，則暫停並在 Planka 發布評論，等待用戶回覆。
    - 若審查通過，則將規格解析並寫入資料庫（`projects` 表），隨後觸發研究流程。

### B. Research Graph (研究工作流圖)
位於 `framework/graph.py`，是系統最核心的部分，負責策略的「開發 -> 測試 -> 修正」迭代。

#### 運作流程與對應函數：
- **Plan** (`plugin.plan_node`): 根據目標制定實作計畫。
- **Implement** (`plugin.implement_node`): 撰寫 Python 程式碼或設定檔。
- **Test** (`plugin.test_node`): 執行回測引擎並收集數據。
- **Analyze** (`_make_analyze_wrapper` 封裝 `plugin.analyze_node`): 判定結果。
    - **PASS**: 執行 `summarize` -> `record_metrics` -> 結束。
    - **FAIL**: 執行 `revise` -> 回到 `implement`。
    - **TERMINATE**: 執行 `record_terminate_metrics` -> `final_summary` -> 結束。

---

## 3. LangGraph 的關鍵特性應用

### 持久化與斷點續傳 (Persistence)
系統在 `build_graph` 中初始化 `PostgresSaver`。
- **實作位置**: `framework/graph.py` 底部與 `framework/spec_review_graph.py` 底部。
- **功能**: 使用 PostgreSQL 記錄每一輪的狀態，當系統崩潰或需要人工介入時，可精確恢復。

### 人機協作 (Human-in-the-Loop, HITL)
- **觸發位置**: 當 `analyze_node` 返回 `needs_human_approval=True` 時。
- **恢復位置**: `framework/api/server.py` 中的 `/resume` 接口，接收 `Command(resume=...)` 並繼續圖表的運行。


### 插件化架構 (Plugin Integration)
`ResearchGraph` 本身是一個框架，它定義了標準的節點接口。具體的業務邏輯（例如量化交易研究）由 `ResearchPlugin` 實作：
- `plugin.plan_node()`
- `plugin.test_node()`
- 這種設計使得系統可以輕易擴展到量化交易以外的其他研究領域。

---

## 4. 數據流向總結

1.  **輸入**：Planka 任務卡片中的 Markdown 規格。
2.  **規格階段 (Spec Review Graph)**：多個 LLM 協作完善規格，確認目標清晰。
3.  **執行階段 (Research Graph)**：
    - LLM 撰寫程式碼 (Python)。
    - 系統在獨立環境執行測試 (Backtest)。
    - 根據測試數據 (Win Rate, Alpha 等) 判定是否達標。
    - 自動迭代修正，直到 PASS 或達到最大循環次數。
4.  **輸出**：最終的研究總結報告（自動上傳回 Planka）與存入資料庫的量化指標。
