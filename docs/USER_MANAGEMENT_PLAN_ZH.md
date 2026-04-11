# 專案流程改善計畫：Planka 多用戶與多租戶支持

## 1. 改善目標
為了解決團隊多人同時進行研究時的衝突與管理需求，本計畫旨在實現：
- **個人研究看板**：每個使用者擁有獨立的 Planka 專案與看板。
- **自動化用戶註冊**：透過 CLI 指令一鍵完成 Planka 登入、看板初始化與資料庫關聯。
- **多租戶 Webhook**：系統能根據看板 ID 自動識別使用者，動態切換 Planka Token。

---

## 2. 實作流程概要

### A. 資料庫架構擴展
在 `deploy/schema.sql` 中新增 `users` 資料表，並將 `projects` 關聯至使用者：
- `users` (id, username, planka_token, planka_board_id, created_at)
- `projects` 新增 `user_id` 欄位。

### B. CLI 用戶註冊指令
新增 `agentic-research add-user` 指令：
1.  **輸入**：Planka 使用者名稱與密碼。
2.  **摘要**：
    - 呼叫 Planka API 取得 Bearer Token。
    - 建立專屬 Project (例如: "Research - [Username]") 與 Board。
    - 初始化看板欄位 (Planning, Verify, Done 等)。
    - 設定 Webhook 同步機制。
3.  **輸出**：將使用者資訊與 Token 寫入資料庫 `users` 表。
4.  **檢查點**：
    - [ ] 使用者可在 Planka 看到新看板。
    - [ ] `users` 表中出現正確的 `planka_board_id`。

### C. 後端多租戶 Webhook 路由
修改 `framework/api/server.py` 中的 Webhook 處理函數：
1.  **輸入**：Planka 傳來的卡片移動事件 (Payload 包含 `board_id`)。
2.  **摘要**：
    - 根據 `board_id` 從 `users` 表中查出該用戶的 `planka_token`。
    - 初始化「請求級別 (Request-scoped)」的 `PlankaSink` 物件。
    - 使用該用戶的 Token 進行後續的留言、附件下載與移動操作。
3.  **檢查點**：
    - [ ] 不同使用者的看板操作能正確觸發各自的研究流程。
    - [ ] 系統操作日誌中顯示正確的用戶身分資訊。

---

## 3. 預期效益
1.  **隔離性**：各研究員的研究卡片互不干擾，資料更為安全。
2.  **擴展性**：未來可支援外部團隊註冊自己的看板進行研究。
3.  **管理便利性**：管理員可透過資料庫輕易檢視各研究員的進度與 Token 狀態。
