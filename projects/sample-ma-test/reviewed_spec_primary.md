# MA Crossover Alpha

## 對需求的理解
此研究想驗證日線級別的快慢均線交叉，是否能在美股大型股上穩定捕捉趨勢動能，並在樣本外期間維持可接受的風險報酬。研究流程包含以 `freqtrade` 執行回測、用網格搜尋挑選均線參數，並以明確的績效門檻作為接受條件。若策略在 OOS 期間無法維持最低獲利因子，則不接受任何參數組合。

## 研究領域
Quantitative Trading Strategy

## 市場論點
Moving average crossover strategy can capture trend momentum in US equities at the daily timeframe. Fast MA crossing above slow MA generates positive expected value with acceptable drawdown and consistent win rate across out-of-sample periods.

## 交易範圍
- 標的：AAPL, MSFT, GOOG, AMZN, NVDA
- 資產類別：equity
- 交易所：NASDAQ *(假設)*
- 方向：long-only *(假設)*
- 時間週期：daily
- 交易時段：美東時間正常盤 09:30-16:00 *(假設)*

## 資料規格
- 資料來源：yfinance
- 必要欄位：open, high, low, close, adj_close, volume *(假設)*
- 訓練期間：2018-01-01 to 2021-12-31 *(假設)*
- 驗證期間：2022-01-01 to 2022-12-31 *(假設)*
- 測試期間：2023-01-01 to 2024-12-31
- 清理規則：依交易日對齊、移除重複列、保留有完整 OHLCV 的資料列、使用調整後價格處理分割與股利影響 *(假設)*

## 進場訊號
- 主要條件：SMA(close, fast_period) crosses above SMA(close, slow_period)
- 參數搜尋：
  - `fast_period`: 5, 10, 15, 20
  - `slow_period`: 40, 60, 80, 100
- 訊號確認：單根 K 棒收盤確認；當日收盤時 SMA 已完成黃金交叉，於下一個交易日開盤執行
- 限制：僅在 `fast_period < slow_period` 的組合上評估 *(假設)*

## 出場訊號
- 主要條件：SMA(close, fast_period) crosses below SMA(close, slow_period)
- 出場確認：以當日收盤完成死亡交叉後成立，於下一個交易日開盤平倉 *(假設)*
- 停損：無額外固定百分比停損，僅依死亡交叉訊號出場 *(假設)*

## 倉位 Sizing 與風險規則
- 單一標的倉位：等權配置，單筆目標倉位 20% 資金 *(假設)*
- 槓桿：0，僅使用現金部位 *(假設)*
- 同一標的：同時僅允許 1 個多頭倉位 *(假設)*
- 組合層級：最多同時持有 5 檔標的，無金字塔加碼 *(假設)*

## 執行假設
- 回測引擎：freqtrade
- 訂單類型：market order *(假設)*
- 滑價：單邊 5 bps *(假設)*
- 手續費：單邊 10 bps *(假設)*
- 延遲：1 bar；訊號於收盤判定、訂單於下一根日線開盤成交 *(假設)*
- 再平衡頻率：daily *(假設)*
- Plugin：quant_alpha
- Review every：5 loops
- Max loops：20

## 最佳化設定
- 方法：Grid search
- 偏好：Prioritize shorter fast periods to reduce overfit.

## 績效門檻
- Sharpe Ratio：年化（252 交易日）>= 1.2
- Max Drawdown：<= 15%
- In-sample Profit Factor：>= 1.3
- Win Rate：>= 48%
- Out-of-sample Profit Factor：>= 1.1
- Min Alpha Ratio：>= 0.0 *(假設)*
- 接受條件：Require OOS profit factor >= 1.1 before accepting any parameter set.

## 假設說明
- **交易所**：NASDAQ — 所列標的皆為美股大型科技股，且均於 NASDAQ 掛牌，可合理收斂為單一交易所假設。
- **方向**：long-only — 原始規格只定義黃金交叉進場與死亡交叉出場，未描述放空邏輯。
- **交易時段**：美東時間正常盤 09:30-16:00 — 日線美股回測的保守預設執行時段。
- **必要欄位**：open, high, low, close, adj_close, volume — 日線股票回測與清理所需的最小完整欄位集合。
- **訓練期間**：2018-01-01 to 2021-12-31 — 原始 in-sample 區間需切出獨立驗證集，因此保留前四年為訓練。
- **驗證期間**：2022-01-01 to 2022-12-31 — 以時間序切出最後一年作為參數選擇驗證集。
- **清理規則**：依交易日對齊、移除重複列、保留完整 OHLCV、使用調整後價格 — 這是股票日線回測的保守資料品質基線。
- **限制**：僅評估 `fast_period < slow_period` — 排除無效或語義相反的均線組合。
- **停損**：無額外固定百分比停損 — 原始規格只描述交叉出場，未提供額外風險截斷條件。
- **單一標的倉位**：20% 等權 — 五檔股票對應最直接且可執行的等權配置。
- **槓桿**：0 — 在未明示融資或保證金需求時採保守現金部位假設。
- **同一標的倉位限制**：同時僅 1 個多頭倉位 — 符合單純 crossover 策略的基線實作。
- **組合層級風險規則**：最多持有 5 檔、無金字塔加碼 — 與標的池大小與簡單日線策略一致。
- **訂單類型**：market order — 對日線訊號的標準保守執行假設。
- **滑價**：單邊 5 bps — 為美股大型權值股日線回測可接受的保守摩擦估計。
- **手續費**：單邊 10 bps — 在未指定券商費率時，用偏保守成本避免高估策略表現。
- **延遲**：1 bar — 將訊號產生與成交切開，避免同 bar 成交的樂觀偏差。
- **再平衡頻率**：daily — 與策略週期及訊號頻率一致。
- **Plugin**：quant_alpha — 依審查規則固定更正，不沿用原始 `quant_strategy`。
- **Min Alpha Ratio**：0.0 — 原始規格未定義 alpha ratio，先採非負 alpha 的最低接受門檻以保留可執行性。
