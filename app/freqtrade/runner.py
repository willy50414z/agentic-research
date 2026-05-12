"""
Freqtrade CLI subprocess wrapper。
Importable library — 無 CLI 入口。
backtest.py 和 freqtrade_cli.py 都直接 import 此模組。
"""
import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def download_data(
    config_path: str,
    userdir: str,
    timerange: str,
    timeframe: str | None = None,
) -> None:
    import json
    from pathlib import Path

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    exchange = config.get("exchange", {}).get("name", "binance")
    expected_data_dir = Path(userdir) / "data" / exchange.lower()

    cmd = [
        "freqtrade", "download-data",
        "--config", config_path,
        "--userdir", userdir,
        "--timerange", timerange,
    ]
    if timeframe:
        cmd += ["--timeframes", timeframe.lower()]
        expected_file = expected_data_dir / f" BTCUSDT-{timeframe.lower()}.*"
    else:
        expected_file = expected_data_dir / "*.feather"
    logger.info("[freqtrade] download-data timerange=%s timeframe=%s userdir=%s", timerange, timeframe, userdir)
    logger.info("[freqtrade] download-data expected data dir: %s (exists: %s)", expected_data_dir, expected_data_dir.exists())

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
    if proc.stderr:
        logger.debug("[freqtrade] download-data stderr:\n%s", proc.stderr[:2000])
    if proc.returncode != 0:
        stderr_preview = "\n".join((proc.stderr or "").splitlines()[:100])
        raise RuntimeError(
            f"freqtrade download-data failed (exit {proc.returncode}):\n{stderr_preview}"
        )

    logger.info("[freqtrade] download-data done, stdout=%s", (proc.stdout or "")[:200])

    data_files = list(expected_data_dir.glob("*.feather")) + list(expected_data_dir.glob("*.json")) + list(expected_data_dir.glob("*.json.gz"))
    logger.info("[freqtrade] download-data found %d files in %s: %s", len(data_files), expected_data_dir, [f.name for f in data_files[:5]])


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
    import json
    from pathlib import Path

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    exchange = config.get("exchange", {}).get("name", "unknown")
    pair_whitelist = config.get("exchange", {}).get("pair_whitelist", [])
    timeframe = config.get("timeframe", "")
    data_dir = Path(userdir) / "data" / exchange.lower()

    logger.info("[freqtrade] backtest config: exchange=%s pairs=%s timeframe=%s", exchange, pair_whitelist, timeframe)
    logger.info("[freqtrade] backtest data dir: %s (exists: %s)", data_dir, data_dir.exists())
    if data_dir.exists():
        data_files = list(data_dir.glob("*"))
        logger.info("[freqtrade] backtest data files: %s", [f.name for f in data_files[:10]])

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

        stderr_preview = "\n".join((proc.stderr or "").splitlines()[:100])
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
