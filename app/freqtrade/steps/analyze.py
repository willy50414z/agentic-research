"""analyze_step — LLM judges metrics and decides PASS/FAIL/TERMINATE."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ._common import (
    ARTIFACTS_DIR,
    _PASS_ALPHA,
    _PASS_MAX_DD,
    _PASS_PROFIT_FACTOR,
    _PASS_WIN_RATE,
    _RULES_PATH,
    _call_llm,
    _load_prompt,
    _mlflow_log,
)

logger = logging.getLogger(__name__)


def _rule_based_analyze(loop, plan, metrics):
    if "oos" in metrics:
        oos  = metrics["oos"]
        is_m = metrics.get("is", {})
        is_pf, oos_pf = is_m.get("profit_factor", 0), oos.get("profit_factor", 0)
        is_wr, oos_wr = is_m.get("win_rate", 0), oos.get("win_rate", 0)
        if is_pf > 0 and oos_pf < is_pf * 0.8:
            return "FAIL", f"Overfitting: OOS pf={oos_pf:.4f} < IS pf={is_pf:.4f} × 0.8"
        if is_wr > 0 and oos_wr < is_wr * 0.8:
            return "FAIL", f"Overfitting: OOS wr={oos_wr:.4f} < IS wr={is_wr:.4f} × 0.8"
        m = oos
    else:
        m = metrics

    win_rate      = m.get("win_rate", 0)
    alpha_ratio   = m.get("alpha_ratio")
    max_dd        = m.get("max_drawdown", 1)
    profit_factor = m.get("profit_factor", 0.0)
    target_wr     = plan.get("target_win_rate", _PASS_WIN_RATE)
    alpha_ok      = (alpha_ratio is None) or (alpha_ratio >= _PASS_ALPHA)

    if (win_rate >= target_wr and alpha_ok
            and max_dd <= _PASS_MAX_DD and profit_factor >= _PASS_PROFIT_FACTOR):
        return "PASS", (
            f"win_rate={win_rate:.4f} ≥ {target_wr}  "
            f"drawdown={max_dd:.4f} ≤ 0.20  "
            f"profit_factor={profit_factor:.4f} ≥ {_PASS_PROFIT_FACTOR}"
        )

    fails = []
    if win_rate      < target_wr:           fails.append(f"win_rate={win_rate:.4f} < {target_wr}")
    if not alpha_ok:                         fails.append(f"alpha={alpha_ratio:.4f} < 1.0")
    if max_dd        > _PASS_MAX_DD:        fails.append(f"drawdown={max_dd:.4f} > 0.20")
    if profit_factor < _PASS_PROFIT_FACTOR: fails.append(f"profit_factor={profit_factor:.4f} < {_PASS_PROFIT_FACTOR}")
    return "FAIL", "Failed: " + "; ".join(fails)


def analyze_step(state: dict) -> dict:
    loop        = state.get("loop_index", 0)
    plan        = state.get("implementation_plan") or {}
    raw_metrics = state.get("test_metrics", {})
    is_metrics_ = raw_metrics.get("is",  raw_metrics)
    oos_metrics_= raw_metrics.get("oos", raw_metrics)
    metrics     = oos_metrics_
    logger.info("[freqtrade] analyze  loop=%d", loop)

    if state.get("last_result") == "TERMINATE":
        return {"last_result": "TERMINATE", "last_reason": state.get("last_reason", "")}

    output_dir  = str(ARTIFACTS_DIR.resolve())
    target_pf   = (
        (state.get("spec") or {}).get("performance", {}).get("is_profit_factor")
        or _PASS_PROFIT_FACTOR
    )
    prompt = _load_prompt("analyze").format(
        strategy_name        = plan.get("strategy_name", "?"),
        params               = json.dumps({k: v for k, v in plan.items()
                                           if k != "target_win_rate"}),
        is_win_rate          = is_metrics_.get("win_rate",      0),
        is_profit_factor     = is_metrics_.get("profit_factor", 0),
        is_max_drawdown      = is_metrics_.get("max_drawdown",  0),
        is_n_trades          = is_metrics_.get("n_trades",      0),
        win_rate             = metrics.get("win_rate",         0),
        alpha_ratio          = metrics.get("alpha_ratio",      0),
        max_drawdown         = metrics.get("max_drawdown",     0),
        profit_factor        = metrics.get("profit_factor",    0.0),
        n_trades             = metrics.get("n_trades",         0),
        target_win_rate      = plan.get("target_win_rate", _PASS_WIN_RATE),
        target_profit_factor = target_pf,
        loop_index           = loop,
        RULES_PATH           = _RULES_PATH,
        OUTPUT_DIR           = output_dir,
    )

    try:
        _call_llm(prompt, cwd=output_dir)
        result_file = Path(output_dir) / "analyze_result.txt"
        lines  = result_file.read_text(encoding="utf-8").strip().splitlines()
        result = (lines[0].strip().upper() if lines else "FAIL")
        reason = (lines[1].strip() if len(lines) > 1 else "")
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] analyze  LLM unavailable (%s) — rule-based fallback", e)
        result, reason = _rule_based_analyze(loop, plan, metrics)
    except Exception as e:
        logger.warning("[freqtrade] analyze  file read failed (%s) — rule-based fallback", e)
        result, reason = _rule_based_analyze(loop, plan, metrics)

    if result not in ("PASS", "FAIL", "TERMINATE"):
        result = "FAIL"

    _mlflow_log(project_id=state.get("project_id", "unknown"), loop=loop,
                plan=plan, metrics=metrics, result=result)
    logger.info("[freqtrade] analyze  result=%s  %s", result, reason)
    return {"last_result": result, "last_reason": reason}
