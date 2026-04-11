---
name: backtest-required-metrics
description: 定義量化策略回測的必要評估指標、門檻來源與決策邏輯，適用於 quant_alpha analyze agent。
type: rule
---

# Backtest Required Metrics

## 必須評估的指標

每次回測分析**必須**包含以下所有指標：

| 指標 | 說明 | 預設最低門檻 |
|------|------|------------|
| `win_rate` | 獲利交易比例（贏的次數 / 總交易次數） | 0.55 |
| `profit_factor` | 總獲利 / 總虧損（gross_profit / gross_loss） | 1.2 |
| `alpha_ratio` | 策略報酬率 / 買入持有報酬率；> 1.0 代表跑贏大盤 | 1.0 |
| `max_drawdown` | 最大回撤（權益曲線峰谷差 / 峰值） | ≤ 0.20 |
| `n_trades` | 總交易次數（過少交易代表結果缺乏統計意義） | 報告但不單獨設門檻 |

## 門檻來源優先序

1. **以 spec 動態值為準**：從已通過審查的 `reviewed_spec_final.md` 的 `## Performance Thresholds` 讀取。
2. **未填寫則使用上表預設值**：若 spec 未指定某指標門檻，使用預設最低門檻。
3. **禁止使用比預設更寬鬆的門檻**：不得自行放寬標準以通過評估。

## 決策邏輯

```
所有指標 >= 門檻  →  PASS
任一指標 < 門檻   →  FAIL
```

**TERMINATE 使用條件（嚴格限制）：**
- 連續多輪 FAIL 且失敗原因相同、參數已嘗試各種方向調整
- 策略結構上有根本缺陷（例如：n_trades = 0，代表策略永遠不進場）
- 不得因「接近門檻但未達標」就 TERMINATE

## profit_factor 計算方式

```
gross_profit  = sum(所有正 PnL)
gross_loss    = abs(sum(所有負 PnL))
profit_factor = gross_profit / gross_loss
若 gross_loss = 0（全勝）：profit_factor = 9.99（上限值）
```

## 報告格式要求

分析結果必須：
1. 列出每個指標的實際值與門檻值
2. 明確標注每個指標是否達標（✅ 或 ❌）
3. 決策說明聚焦於指標數值，不使用模糊詞
