"""
從 spec dict 動態生成 Freqtrade config.json。
Importable library — 無 CLI 入口。
"""
import json
from pathlib import Path


def generate_config(spec: dict, work_dir: Path) -> Path:
    """
    Generate Freqtrade config.json from spec fields and write to work_dir.
    Raises KeyError if required spec fields are missing.
    Returns path to the written config.json.
    """
    scope     = spec["trading_scope"]
    execution = spec.get("execution", {})

    pair      = scope["pair"]
    timeframe = scope["timeframe"]
    exchange  = scope["exchange"]

    # Parse fee: "0.10%" → 0.001
    fee_str = str(execution.get("fee", "0.1%")).rstrip("%")
    fee_pct = float(fee_str) / 100

    # Derive stake_currency from pair: "BTC/USDT" → "USDT"
    stake_currency = pair.split("/")[1] if "/" in pair else "USDT"

    config = {
        "max_open_trades": 1,
        "stake_currency": stake_currency,
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 1.0,
        "fiat_display_currency": "USD",
        "timeframe": timeframe,
        "dry_run": True,
        "dry_run_wallet": 1000,
        "trading_mode": "spot",
        "margin_mode": "",
        "exchange": {
            "name": exchange,
            "key": "",
            "secret": "",
            "ccxt_config": {},
            "ccxt_async_config": {},
            "pair_whitelist": [pair],
        },
        "fee": fee_pct,
        "internals": {},
    }

    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path
