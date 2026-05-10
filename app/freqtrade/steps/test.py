"""test_step — OOS backtest run (mock or real).

In ``mock`` mode this generates synthetic OOS metrics; in ``real`` mode
the OOS metrics are already produced by ``implement_step`` (real path
runs IS+OOS together) so this step only formats them.
"""
from __future__ import annotations

import hashlib
import logging
import random as _random

from ._common import BACKTEST_MODE

logger = logging.getLogger(__name__)


def _mock_test_result(state: dict) -> dict:
    plan    = state.get("implementation_plan", {}) or {}
    attempt = state.get("attempt_count", 0) + 1
    n_bars  = 300 + attempt * 50

    seed_input = f"{n_bars}{plan.get('strategy_name', '')}{sorted(plan.items())}"
    seed = int(hashlib.md5(seed_input.encode()).hexdigest(), 16) % 100_000
    rng  = _random.Random(seed)

    n_trades     = rng.randint(20, 80)
    win_rate     = round(rng.uniform(0.45, 0.75), 4)
    total_return = round(rng.uniform(-0.10, 0.40), 4)
    alpha_ratio  = round(rng.uniform(0.7, 2.5), 4)
    max_drawdown = round(rng.uniform(0.05, 0.30), 4)
    gross_profit = round(rng.uniform(0.1, 0.5), 4)
    gross_loss   = round(rng.uniform(0.05, 0.4), 4)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 1e-9 else 9.99

    return {
        "attempt_count": attempt,
        "test_metrics": {
            "win_rate":      win_rate,
            "alpha_ratio":   alpha_ratio,
            "max_drawdown":  max_drawdown,
            "n_trades":      n_trades,
            "total_return":  total_return,
            "profit_factor": profit_factor,
        },
    }


def test_step(state: dict) -> dict:
    loop    = state.get("loop_index", 0)
    attempt = state.get("attempt_count", 0) + 1
    plan    = state.get("implementation_plan") or {}
    logger.info("[freqtrade] test  loop=%d  attempt=%d", loop, attempt)

    if BACKTEST_MODE == "mock":
        return _mock_test_result(state)

    oos = state.get("oos_metrics", {})
    return {
        "attempt_count": attempt,
        "test_metrics": {"is": state.get("is_metrics", {}), "oos": oos},
    }
