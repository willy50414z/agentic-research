# 01-01 Turtle Trading (海龜交易法)

## 策略類型
**01 — Trend Following（趨勢跟蹤）**

---

## 概述

海龜交易法是量化交易史上最著名的實驗成果之一。1983 年，商品交易大師 **Richard Dennis** 與搭檔 **William Eckhardt** 打賭：「偉大的交易員是天生的還是可以訓練出來的？」Dennis 隨後招募了一批毫無交易經驗的素人（稱為「海龜」），教授他們一套完整的規則化趨勢跟蹤系統，結果這批學員在 5 年內為 Dennis 賺進超過 1 億美元的利潤，完美驗證了規則化交易的可行性。

---

## 核心邏輯

> **「順勢而為，讓利潤奔跑，快速止損。」**

策略基於以下信念：
1. 市場會出現持續性的價格趨勢
2. 在突破關鍵高低點時順勢進場可以捕捉這些趨勢
3. 透過嚴格的倉位控管，單筆虧損不影響整體生存

---

## 關鍵指標

| 指標 | 說明 |
|------|------|
| **唐奇安通道 (Donchian Channel)** | 以過去 N 日的最高價為上軌、最低價為下軌 |
| **真實波動幅度 (ATR)** | Average True Range，衡量市場波動性，用於計算倉位大小 |

---

## 交易規則（System 1 & System 2）

海龜系統分兩套，可同時使用。在量化實作中，建議將週期設為變數以便優化：

### System 1（短線趨勢）
| 項目 | 規則 | 量化定義 (標籤: `Param_S1`) |
|------|------|------|
| **進場** | 價格~~突破過去 **20 日**最高點~~ | `High > High[1...20].max()` (盤中觸發) |
| **出場** | 價格~~跌破過去 **10 日**最低點~~ | `Low < Low[1...10].min()` (盤中觸發) |
| **過濾** | ~~若上一次突破交易是盈利的，本次跳過~~ | 可選：`if last_trade_profit > 0: skip_current_signal = True` |

### System 2（長線趨勢）
| 項目 | 規則 | 量化定義 (標籤: `Param_S2`) |
|------|------|------|
| **進場** | 價格~~突破過去 **55 日**最高點~~ | `High > High[1...55].max()` |
| **出場** | 價格~~跌破過去 **20 日**最低點~~ | `Low < Low[1...20].min()` |

---

## 倉位管理與風險控管 (標籤: `Risk_Mgmt`)

海龜法則使用 **ATR 單位（Unit）** 控制每筆交易風險，這是量化調優的核心：

1. **基礎單位計算**：
```python
N = ATR(period=20) # 原版為 20 日 EMA(TR)
Unit = (Account_Equity * Risk_Percent) / (N * Contract_Multiplier)
# Risk_Percent 預設 0.01 (1%)，適合進行參數掃描 (0.005 - 0.02)
```

2. **加倉與停損邏輯**：
- **加倉 (Pyramiding)**：進場後每上漲 `0.5 * N` 加倉一個 Unit，最多累積至 4 Units。
- **動態停損**：
  - 初始停損：`Entry_Price - 2 * N`
  - 若有加倉：所有 Units 的停損隨最新加倉位上移，保持 `Last_Entry_Price - 2 * N`。

---

## 量化優化方向 (Optimization Hooks)

在進行 PCA 或參數調優時，建議重點關注：
1. **DC_Entry_Period**: [10, 20, 30, 55, 100] (影響趨勢捕捉的敏感度)
2. **DC_Exit_Period**: [5, 10, 20] (影響獲利回吐的程度)
3. **ATR_Multiplier**: [1.5, 2.0, 3.0] (影響停損寬度與加倉頻率)
4. **Volatility_Filter**: 增加一個過濾條件，如 `ADX > 20` 才啟動海龜系統，以避開橫盤震盪。

---

## 量化實作要點

```python
# 偽代碼示意
ATR_20 = average_true_range(period=20)
DC_20H = rolling_max(high, period=20)
DC_10L = rolling_min(low, period=10)

unit_size = account_equity * 0.01 / ATR_20

if close > DC_20H[-1]:  # 突破20日高點
    buy(unit_size)
    stop_loss = entry_price - 2 * ATR_20

if close < DC_10L[-1]:  # 跌破10日低點
    close_long()
```

**實作注意事項：**
- 在加密貨幣市場中，因波動率較高，週期通常縮短（如 10/5 日替代 20/10 日）
- 需處理跳空開盤導致的滑點問題
- 原始規則為期貨市場設計，加密現貨需調整資金費率影響

---

## 量化研究補充（必要條件 / 可選條件）

- 必要條件：突破訊號須以已完成 K 線計算，避免把盤中創高又回落誤當有效突破。
- 必要條件：回測需納入手續費、滑點、合約乘數與最小交易單位，否則 ATR 倉位 sizing 會失真。
- 必要條件：建議額外記錄 `breakout_distance / ATR`、`ADX`、`Donchian_Width`，供後續 PCA 與 regime filter 使用。
- 可選條件：加入跨市場相對強弱過濾，只交易同池資產中強勢標的。
- 可選條件：加入波動率壓縮前置條件，例如突破前 `ATR_percentile < 60%`。
- 可選條件：把 pyramiding 觸發由固定 `0.5N` 改成可調參數，測試不同加倉節奏的風險報酬。

---

## 優缺點

| 優點 | 缺點 |
|------|------|
| 規則完全明確，可 100% 自動化 | 震盪行情中假突破多，勝率通常低於 40% |
| 資金管理嚴謹，不會爆倉 | 需要心理承受長時間的小虧損積累 |
| 歷史回測表現穩健 | 在高頻、低趨勢的市場效果差 |

---

## 延伸閱讀

- *Way of the Turtle* — Curtis Faith（原版海龜成員之一的著作）
- *Complete TurtleTrader* — Michael Covel
- 原版海龜規則文件：[Original Turtle Rules](https://www.turtletrader.com/turtle-rules.pdf)
- ATR 計算公式：`ATR = max(High-Low, |High-PrevClose|, |Low-PrevClose|)`
