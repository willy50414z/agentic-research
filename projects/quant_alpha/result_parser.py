# projects/quant_alpha/result_parser.py
"""
解析 Freqtrade backtest .zip，生成指標 JSON、交易記錄、訊號 JSON、HTML 報告。
Importable library — 無 CLI 入口。
"""
import json
import zipfile
from pathlib import Path


def parse_backtest_zip(zip_path: Path, strategy_name: str) -> dict:
    """
    Extract metrics from a Freqtrade backtest .zip.
    Returns dict with: win_rate, profit_factor, max_drawdown,
                       profit_total_pct, n_trades, trades (list).
    Raises ValueError on bad zip or missing strategy.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            json_names = [
                n for n in zf.namelist()
                if n.endswith(".json")
                and not n.endswith(".meta.json")
                and not n.endswith("_config.json")
            ]
            if not json_names:
                raise ValueError(f"No main JSON in {zip_path}")
            raw = json.loads(zf.read(json_names[0]).decode("utf-8", errors="replace"))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid zip: {zip_path}: {e}") from e

    strategy_data = raw.get("strategy", {}).get(strategy_name)
    if strategy_data is None:
        available = list(raw.get("strategy", {}).keys())
        raise ValueError(
            f"Strategy '{strategy_name}' not found in {zip_path}. "
            f"Available: {available}"
        )

    return {
        "win_rate":          round(float(strategy_data.get("winrate", 0)), 6),
        "profit_factor":     round(float(strategy_data.get("profit_factor", 0)), 6),
        "max_drawdown":      round(float(strategy_data.get("max_drawdown_account", 0)), 6),
        "profit_total_pct":  round(float(strategy_data.get("profit_total", 0)) * 100, 4),
        "n_trades":          int(strategy_data.get("total_trades", 0)),
        "trades":            list(strategy_data.get("trades", [])),
    }


def write_loop_artifacts(
    is_metrics: dict,
    oos_metrics: dict,
    work_dir: Path,
    loop: int,
) -> None:
    """
    Write all loop artifacts to work_dir:
      loop_{N}_is.json, loop_{N}_oos.json,
      loop_{N}_trades.json, loop_{N}_signals.json, loop_{N}_report.html
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    is_clean  = {k: v for k, v in is_metrics.items()  if k != "trades"}
    oos_clean = {k: v for k, v in oos_metrics.items() if k != "trades"}

    (work_dir / f"loop_{loop}_is.json").write_text(
        json.dumps(is_clean, indent=2), encoding="utf-8")
    (work_dir / f"loop_{loop}_oos.json").write_text(
        json.dumps(oos_clean, indent=2), encoding="utf-8")

    trades = oos_metrics.get("trades", [])
    (work_dir / f"loop_{loop}_trades.json").write_text(
        json.dumps(trades, indent=2), encoding="utf-8")

    signals = [
        {
            "pair":         t.get("pair"),
            "enter_date":   t.get("open_date"),
            "exit_date":    t.get("close_date"),
            "enter_rate":   t.get("open_rate"),
            "exit_rate":    t.get("close_rate"),
            "profit_ratio": t.get("profit_ratio"),
        }
        for t in trades
    ]
    (work_dir / f"loop_{loop}_signals.json").write_text(
        json.dumps(signals, indent=2), encoding="utf-8")

    html = _build_html_report(loop, is_clean, oos_clean, trades)
    (work_dir / f"loop_{loop}_report.html").write_text(html, encoding="utf-8")


def _build_html_report(loop: int, is_m: dict, oos_m: dict, trades: list) -> str:
    def _row(k: str, v) -> str:
        return f"<tr><td>{k}</td><td>{v}</td></tr>"

    metric_rows = "\n".join([
        _row("win_rate (IS)",        is_m.get("win_rate", 0)),
        _row("win_rate (OOS)",       oos_m.get("win_rate", 0)),
        _row("profit_factor (IS)",   is_m.get("profit_factor", 0)),
        _row("profit_factor (OOS)",  oos_m.get("profit_factor", 0)),
        _row("max_drawdown (IS)",    is_m.get("max_drawdown", 0)),
        _row("max_drawdown (OOS)",   oos_m.get("max_drawdown", 0)),
        _row("n_trades (IS)",        is_m.get("n_trades", 0)),
        _row("n_trades (OOS)",       oos_m.get("n_trades", 0)),
    ])
    trade_rows = "\n".join([
        f"<tr><td>{t.get('pair','')}</td><td>{t.get('open_date','')}</td>"
        f"<td>{t.get('close_date','')}</td>"
        f"<td>{t.get('profit_ratio', 0):.4f}</td></tr>"
        for t in trades[:100]
    ])

    return (
        f"<!DOCTYPE html><html><head><title>Loop {loop} Backtest Report</title>"
        f"<style>table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px}}"
        f"</style></head><body>"
        f"<h1>Loop {loop} Backtest Report</h1>"
        f"<h2>Metrics</h2>"
        f"<table><tr><th>Metric</th><th>Value</th></tr>{metric_rows}</table>"
        f"<h2>OOS Trades (first 100)</h2>"
        f"<table><tr><th>Pair</th><th>Open</th><th>Close</th><th>Profit</th></tr>"
        f"{trade_rows}</table></body></html>"
    )
