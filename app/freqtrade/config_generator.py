"""
從 spec dict 動態生成 Freqtrade config.json。
Importable library — 無 CLI 入口。
"""
import json
from pathlib import Path


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base. Returns a new dict."""
    result = dict(base)
    for key, val in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def generate_config(spec: dict, work_dir: Path, plan: dict | None = None) -> Path:
    """
    Generate Freqtrade config.json from spec fields and write to work_dir.
    Supports both trading_scope (new) and universe (legacy) key names.
    If plan contains a 'config_overrides' dict, it is deep-merged on top of
    the base config, allowing the LLM to set strategy-specific parameters
    (e.g. entry_pricing.price_side for market-order strategies).
    Returns path to the written config.json.
    """
    scope = spec.get("trading_scope") or spec.get("universe") or {}
    if not scope:
        raise KeyError(
            "spec is missing 'trading_scope' (and legacy 'universe') — "
            "ensure parse_spec_md ran against a reviewed spec with trading range section."
        )
    execution = spec.get("execution", {})

    pair      = scope.get("pair") or scope.get("instruments", "")
    timeframe = scope.get("timeframe", "")
    exchange  = scope.get("exchange", "")
    if not pair:
        raise KeyError("spec trading_scope is missing 'pair' — check reviewed spec content.")
    if not timeframe:
        raise KeyError("spec trading_scope is missing 'timeframe'.")
    if not exchange:
        raise KeyError("spec trading_scope is missing 'exchange'.")

    # Parse fee: "0.10%" → 0.001, or float 0.001 → 0.001
    fee_raw = execution.get("fee", "0.1%")
    if isinstance(fee_raw, str):
        fee_pct = float(fee_raw.rstrip("%")) / 100
    elif isinstance(fee_raw, (int, float)):
        fee_pct = float(fee_raw)
    else:
        raise TypeError(f"fee must be a percentage string or float, got {type(fee_raw)}")

    # Derive stake_currency from pair: "BTC/USDT" → "USDT"
    stake_currency = pair.split("/")[1] if "/" in pair else "USDT"

    config = {
        "max_open_trades": 1,
        "stake_currency": stake_currency,
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 1.0,
        "fiat_display_currency": "USD",
        "timeframe": timeframe.lower(),
        "dry_run": True,
        "dry_run_wallet": 1000,
        "trading_mode": "spot",
        "margin_mode": "",
        "exchange": {
            "name": exchange.lower(),
            "key": "",
            "secret": "",
            "ccxt_config": {
                "options": {"defaultType": "spot"},
            },
            "ccxt_async_config": {
                "options": {"defaultType": "spot"},
            },
            "pair_whitelist": [pair],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {
            "price_side": "other",
            "use_order_book": False,
            "order_book_top": 1,
        },
        "exit_pricing": {
            "price_side": "other",
            "use_order_book": False,
            "order_book_top": 1,
        },
        "fee": fee_pct,
        "internals": {},
    }

    if plan:
        overrides = plan.get("config_overrides") or {}
        if overrides:
            config = _deep_merge(config, overrides)

    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path
