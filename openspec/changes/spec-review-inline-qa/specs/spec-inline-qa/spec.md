## ADDED Requirements

### Requirement: Q&A section format in spec.md
spec.md 的 Q&A 區段 SHALL 使用固定格式，以便 LLM 和系統可靠地偵測與解析。

格式規範：
- 區段以 `## 待釐清問題（請直接在每個問題下方填入回答）` 為 header
- 每個問題以 `**Q<n>：<問題內容>**` 開頭（獨立行）
- 緊接一個空行，再接 `> 回答：`（引用格式）
- User 在 `> 回答：` 同一行或下方填入回答
- Q&A section 前 SHALL 有一條 `---` 水平線作為分隔

#### Scenario: LLM 寫入問題區段
- **WHEN** LLM 在 `need_update` 時產出含問題的 spec
- **THEN** spec.md 底部 SHALL 包含符合上述格式的 `## 待釐清問題` section

#### Scenario: User 填寫回答
- **WHEN** User 在 `> 回答：` 後填入文字並儲存 spec.md
- **THEN** 系統重新觸發時 SHALL 能解析每個問題與對應回答

#### Scenario: 乾淨版本不含問題區段
- **WHEN** LLM 在 `status_pass.txt` 路徑產出最終 spec
- **THEN** 輸出的 `reviewed_spec_final.md` SHALL 不含 `## 待釐清問題` section 及 `---` 分隔線

### Requirement: Q&A 區段偵測
系統 SHALL 在 spec review 啟動時自動偵測 spec.md 是否含有待回答的 Q&A 區段。

#### Scenario: 偵測到 Q&A 區段
- **WHEN** spec.md 包含 `## 待釐清問題` header
- **THEN** `_spec_review_init` SHALL 設定 `has_pending_qa = True`

#### Scenario: 未偵測到 Q&A 區段
- **WHEN** spec.md 不含 `## 待釐清問題` header
- **THEN** `_spec_review_init` SHALL 設定 `has_pending_qa = False`，走一般 review 流程
