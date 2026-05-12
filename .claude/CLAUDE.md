# Shared Rules & Skills

This project uses the `agent_cli_file` knowledge base as a submodule.

At session start, load the shared rules and skills index:

@knowledge-base/agent_cli_file/catalogue.md
@.ai/rules/spec-review.md

## Skills

- **debug-card** (`.claude/skills/debug-card/SKILL.md`) — 讀取最新錯誤報告或依 card_id 查詢 DB，呈現完整診斷 context 供 Claude 分析。
  觸發：使用者輸入 `/debug-card` 或 `/debug-card {card_id}` 時，invoke `Skill` tool with `skill: "debug-card"`。

- **trade-strategy-freqtrade-implementation** — 實作 Freqtrade 策略的必填清單，含 `order_types` 四鍵規範、look-ahead bias 陷阱、config 生成與完成檢查。
  觸發：建立或修改任何 Freqtrade strategy `.py` 檔案之前，必須先 invoke `Skill` tool with `skill: "trade-strategy-freqtrade-implementation"`。

- **backtest-preflight** — 執行 backtest 前的三道 gate：data readiness、look-ahead bias、config 完整性（含 `order_types` 四鍵檢查）。
  觸發：執行 `freqtrade backtesting` 或 `--mode backtest` 之前，必須先 invoke `Skill` tool with `skill: "backtest-preflight"`。
