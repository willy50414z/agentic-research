"""
Freqtrade CLI subprocess wrapper。
Importable library — 無 CLI 入口。
backtest.py 和 freqtrade_cli.py 都直接 import 此模組。
"""
import subprocess
import time
from pathlib import Path


def run_freqtrade_backtest(
    strategy_name: str,
    strategy_dir: str,
    config_path: str,
    userdir: str,
    timerange: str,
    results_dir: str,
    max_retries: int = 3,
) -> Path:
    """
    Call freqtrade backtesting CLI for a single timerange.
    Returns Path to the newly created .zip result file.
    Raises FileNotFoundError if freqtrade CLI is not installed.
    Raises RuntimeError on non-zero exit after max_retries.
    Raises ValueError if no new .zip is detected after success.
    """
    Path(results_dir).mkdir(parents=True, exist_ok=True)

    def _list_zips() -> set[Path]:
        return set(Path(results_dir).glob("*.zip"))

    before = _list_zips()

    cmd = [
        "freqtrade", "backtesting",
        "--config", config_path,
        "--strategy", strategy_name,
        "--strategy-path", strategy_dir,
        "--userdir", userdir,
        "--timerange", timerange,
        "--export", "trades",
        "--cache", "none",
    ]

    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                "freqtrade CLI not found. Install with: pip install freqtrade"
            )

        if proc.returncode == 0:
            break

        stderr_preview = "\n".join((proc.stderr or "").splitlines()[:50])
        if attempt == max_retries:
            raise RuntimeError(
                f"freqtrade exited with code {proc.returncode} "
                f"(attempt {attempt}/{max_retries}):\n{stderr_preview}"
            )
        time.sleep(2 ** attempt)

    after = _list_zips()
    new_zips = after - before
    if not new_zips:
        raise ValueError(
            f"freqtrade completed but no new .zip found in {results_dir}"
        )
    return max(new_zips, key=lambda p: p.stat().st_mtime)
