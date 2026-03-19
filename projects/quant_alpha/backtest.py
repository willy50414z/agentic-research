"""
projects/quant_alpha/backtest.py

Pure-Python backtest engine (no numpy / pandas).

Supported strategies:
  - rsi_momentum  : buy when RSI < entry_threshold*100, sell when RSI > exit_threshold*100
  - ma_crossover  : buy when fast-MA crosses above slow-MA, sell on reverse
  - breakout      : buy on N-bar high breakout, sell on N-bar low breakdown

Usage:
    from projects.quant_alpha.backtest import run_backtest

    result = run_backtest({
        "strategy_type":   "rsi_momentum",
        "lookback":        14,
        "entry_threshold": 0.30,   # RSI < 30  → buy
        "exit_threshold":  0.70,   # RSI > 70  → sell
        "stop_loss_pct":   0.05,
    })
    # result: {"win_rate": 0.58, "alpha_ratio": 1.23, "max_drawdown": 0.12, "n_trades": 42}
"""

import hashlib
import math
import random
from typing import Any


# ---------------------------------------------------------------------------
# Synthetic price generator
# ---------------------------------------------------------------------------

def _generate_prices(n_bars: int, seed: int = 42) -> list[float]:
    """Random-walk close prices, seeded for reproducibility."""
    rng = random.Random(seed)
    price = 100.0
    prices = [price]
    for _ in range(n_bars - 1):
        pct = rng.gauss(0.0002, 0.015)   # slight upward drift
        price = max(1.0, price * (1 + pct))
        prices.append(round(price, 4))
    return prices


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _sma(prices: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        out.append(sum(prices[i - period + 1: i + 1]) / period)
    return out


def _rsi(prices: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * period
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(prices)):
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(round(100 - 100 / (1 + rs), 4))
        idx = i - period
        avg_gain = (avg_gain * (period - 1) + gains[idx + period - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx + period - 1]) / period

    return out


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------

def _signals_rsi(prices, lookback, entry_threshold, exit_threshold):
    """1 = buy, -1 = sell, 0 = hold"""
    rsi = _rsi(prices, lookback)
    signals = [0] * len(prices)
    entry_level = entry_threshold * 100   # e.g. 0.30 → 30
    exit_level  = exit_threshold  * 100   # e.g. 0.70 → 70
    for i in range(lookback, len(prices)):
        if rsi[i] is not None:
            if rsi[i] < entry_level:
                signals[i] = 1
            elif rsi[i] > exit_level:
                signals[i] = -1
    return signals


def _signals_ma_crossover(prices, lookback, **_):
    fast_period = max(2, lookback // 3)
    slow_period = lookback
    fast = _sma(prices, fast_period)
    slow = _sma(prices, slow_period)
    signals = [0] * len(prices)
    for i in range(1, len(prices)):
        if fast[i] is None or slow[i] is None:
            continue
        if fast[i] > slow[i] and (fast[i - 1] is None or fast[i - 1] <= (slow[i - 1] or 0)):
            signals[i] = 1
        elif fast[i] < slow[i] and (fast[i - 1] is None or fast[i - 1] >= (slow[i - 1] or 0)):
            signals[i] = -1
    return signals


def _signals_breakout(prices, lookback, **_):
    signals = [0] * len(prices)
    for i in range(lookback, len(prices)):
        window = prices[i - lookback: i]
        if prices[i] > max(window):
            signals[i] = 1
        elif prices[i] < min(window):
            signals[i] = -1
    return signals


# ---------------------------------------------------------------------------
# Trade simulator
# ---------------------------------------------------------------------------

def _simulate(prices: list[float], signals: list[int], stop_loss_pct: float):
    """
    Simple long-only simulator.
    Returns list of (entry_price, exit_price, pnl_pct).
    """
    trades = []
    position: float | None = None

    for i in range(1, len(prices)):
        if position is None and signals[i] == 1:
            position = prices[i]
        elif position is not None:
            # Stop-loss check
            if prices[i] <= position * (1 - stop_loss_pct):
                pnl = (prices[i] - position) / position
                trades.append((position, prices[i], pnl))
                position = None
            # Exit signal
            elif signals[i] == -1:
                pnl = (prices[i] - position) / position
                trades.append((position, prices[i], pnl))
                position = None

    # Close any open position at end
    if position is not None:
        pnl = (prices[-1] - position) / position
        trades.append((position, prices[-1], pnl))

    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _max_drawdown(prices: list[float]) -> float:
    peak = prices[0]
    max_dd = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _equity_curve(prices: list[float], trades: list[tuple]) -> list[float]:
    """Simplified: accumulate trade PnLs as a running equity multiplier."""
    equity = 1.0
    curve = [equity]
    for _, _, pnl in trades:
        equity *= (1 + pnl)
        curve.append(round(equity, 6))
    return curve


def _alpha_ratio(strategy_return: float, prices: list[float]) -> float:
    """Strategy total return / buy-and-hold total return."""
    bah = (prices[-1] - prices[0]) / prices[0]
    if abs(bah) < 1e-9:
        return 1.0
    return round((1 + strategy_return) / (1 + bah), 4)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_backtest(params: dict[str, Any], n_bars: int = 1000) -> dict[str, Any]:
    """
    Run a backtest with the given strategy parameters.

    Args:
        params: dict with keys:
            strategy_type   : "rsi_momentum" | "ma_crossover" | "breakout"
            lookback        : int (5–50)
            entry_threshold : float (0.1–0.5)  — RSI only
            exit_threshold  : float (0.5–0.9)  — RSI only
            stop_loss_pct   : float (0.02–0.15)
        n_bars: number of synthetic price bars

    Returns:
        dict with win_rate, alpha_ratio, max_drawdown, n_trades, total_return
    """
    strategy_type   = params.get("strategy_type", "rsi_momentum")
    lookback        = int(params.get("lookback", 14))
    entry_threshold = float(params.get("entry_threshold", 0.30))
    exit_threshold  = float(params.get("exit_threshold", 0.70))
    stop_loss_pct   = float(params.get("stop_loss_pct", 0.05))

    # Deterministic seed from params — use MD5 to avoid PYTHONHASHSEED randomization
    seed_str = f"{strategy_type}{lookback}{entry_threshold}{exit_threshold}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % 10_000

    prices = _generate_prices(n_bars, seed=seed)

    if strategy_type == "rsi_momentum":
        signals = _signals_rsi(prices, lookback, entry_threshold, exit_threshold)
    elif strategy_type == "ma_crossover":
        signals = _signals_ma_crossover(prices, lookback)
    elif strategy_type == "breakout":
        signals = _signals_breakout(prices, lookback)
    else:
        signals = _signals_rsi(prices, lookback, entry_threshold, exit_threshold)

    trades = _simulate(prices, signals, stop_loss_pct)

    if not trades:
        return {
            "win_rate": 0.0, "alpha_ratio": 0.0,
            "max_drawdown": 0.0,
            "n_trades": 0, "total_return": 0.0,
        }

    wins         = sum(1 for _, _, pnl in trades if pnl > 0)
    total_return = sum(pnl for _, _, pnl in trades)
    win_rate     = round(wins / len(trades), 4)
    alpha        = _alpha_ratio(total_return, prices)
    # Drawdown on strategy equity curve (not raw prices)
    equity       = _equity_curve(prices, trades)
    max_dd       = _max_drawdown(equity)

    return {
        "win_rate":     win_rate,
        "alpha_ratio":  alpha,
        "max_drawdown": round(max_dd, 4),
        "n_trades":     len(trades),
        "total_return": round(total_return, 4),
    }
