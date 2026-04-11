"""
projects/quant_alpha/backtest.py

Stub backtest engine — returns deterministic fake metrics.
Will be replaced with a real Freqtrade runner in a future iteration.

Usage:
    from projects.quant_alpha.backtest import run_backtest

    result = run_backtest(params, n_bars=1000)
    # result: {"win_rate": ..., "alpha_ratio": ..., "max_drawdown": ...,
    #          "n_trades": ..., "total_return": ..., "profit_factor": ...}
"""

import hashlib
import random
from typing import Any


def run_backtest(params: dict[str, Any], n_bars: int = 1000) -> dict[str, Any]:
    """
    Stub: returns deterministic fake metrics based on params and n_bars.
    Strategy type and internal parameters are intentionally ignored —
    the real Freqtrade runner will use them.
    """
    seed_input = f"{n_bars}{params.get('strategy_name', '')}{sorted(params.items())}"
    seed = int(hashlib.md5(seed_input.encode()).hexdigest(), 16) % 100_000
    rng = random.Random(seed)

    n_trades     = rng.randint(20, 80)
    win_rate     = round(rng.uniform(0.45, 0.75), 4)
    total_return = round(rng.uniform(-0.10, 0.40), 4)
    alpha_ratio  = round(rng.uniform(0.7, 2.5), 4)
    max_drawdown = round(rng.uniform(0.05, 0.30), 4)

    gross_profit = round(rng.uniform(0.1, 0.5), 4)
    gross_loss   = round(rng.uniform(0.05, 0.4), 4)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 1e-9 else 9.99

    return {
        "win_rate":      win_rate,
        "alpha_ratio":   alpha_ratio,
        "max_drawdown":  max_drawdown,
        "n_trades":      n_trades,
        "total_return":  total_return,
        "profit_factor": profit_factor,
    }
