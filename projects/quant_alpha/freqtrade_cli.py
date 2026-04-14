# projects/quant_alpha/freqtrade_cli.py
"""
freqtrade_cli.py — CLI entry point for Freqtrade subcommand dispatch.

LLM 在 implement_node 中透過 subprocess 呼叫此檔案：
    python projects/quant_alpha/freqtrade_cli.py backtest \
        --spec path/to/spec.json --plan path/to/plan_output.json \
        --work-dir path/to/work --userdir path/to/user_data --loop N

此檔案 import freqtrade_runner 和 result_parser（不重複實作 subprocess 邏輯）。
"""
import argparse
import json
import sys
from pathlib import Path

from projects.quant_alpha.backtest import run_backtest_is_oos
from projects.quant_alpha.result_parser import write_loop_artifacts


def dispatch(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="freqtrade_cli",
        description="Freqtrade subcommand dispatcher for agentic backtest loop",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── backtest ──────────────────────────────────────────────────────────────
    bt = sub.add_parser("backtest", help="Run IS/OOS backtest")
    bt.add_argument("--spec",     required=True, help="Path to spec JSON file")
    bt.add_argument("--plan",     required=True, help="Path to plan_output JSON file")
    bt.add_argument("--work-dir", required=True, dest="work_dir", help="Per-loop working directory")
    bt.add_argument("--userdir",  required=True, help="Freqtrade user_data directory")
    bt.add_argument("--loop",     required=True, type=int, help="Loop index (for artifact naming)")

    args = parser.parse_args(argv)

    if args.subcommand == "backtest":
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        work_dir = Path(args.work_dir)
        userdir  = Path(args.userdir)

        is_metrics, oos_metrics = run_backtest_is_oos(
            spec=spec,
            plan=plan,
            work_dir=work_dir,
            userdir=userdir,
        )
        write_loop_artifacts(is_metrics, oos_metrics, work_dir, loop=args.loop)
        print(f"[freqtrade_cli] backtest done: "
              f"IS wr={is_metrics['win_rate']:.4f} | OOS wr={oos_metrics['win_rate']:.4f}")


if __name__ == "__main__":
    dispatch(sys.argv[1:])
