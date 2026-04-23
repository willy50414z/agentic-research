"""extract_metrics.py — 讀取 artifact JSON，輸出統計摘要。

Usage:
    python extract_metrics.py --mode mock --artifacts-dir ./artifacts --output /path/metrics.json
"""
import argparse
import glob
import json
import re
import sys
from pathlib import Path


# ── 共用 ─────────────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = ["win_rate", "profit_factor", "max_drawdown", "n_trades"]


def _extract_loop_num(filename: str) -> str:
    m = re.search(r"loop_(\d+)", filename)
    return m.group(1) if m else "0"


# ── Mock 模式 ─────────────────────────────────────────────────────────────────

def extract_mock_metrics(artifacts_dir: Path) -> dict:
    """讀取 artifacts/loop_*_train.json，回傳統計摘要 dict。"""
    pattern = str(artifacts_dir / "loop_*_train.json")
    files = sorted(glob.glob(pattern), key=lambda p: int(_extract_loop_num(Path(p).name)))
    if not files:
        return {"error": f"No loop_*_train.json found in {artifacts_dir}"}

    result = {"mode": "mock", "loops_found": len(files)}
    for fpath in files:
        raw = json.loads(Path(fpath).read_text(encoding="utf-8"))
        is_result = raw.get("is_result", raw)
        loop_num = _extract_loop_num(Path(fpath).name)
        missing = [k for k in _REQUIRED_FIELDS if k not in is_result]
        result[f"loop_{loop_num}"] = {
            k: is_result.get(k) for k in _REQUIRED_FIELDS + ["total_return", "alpha_ratio"]
        }
        result["missing_fields"] = missing
        result["source_file"] = str(fpath)
    return result


# ── Real 模式 ─────────────────────────────────────────────────────────────────

def extract_real_metrics(artifacts_dir: Path) -> dict:
    """讀取 artifacts/.llm_io/*/loop_*_is.json + oos.json，回傳 IS/OOS 統計 dict。"""
    is_files  = sorted(glob.glob(str(artifacts_dir / ".llm_io" / "*" / "loop_*_is.json")))
    oos_files = sorted(glob.glob(str(artifacts_dir / ".llm_io" / "*" / "loop_*_oos.json")))

    if not is_files:
        return {"error": f"No loop_*_is.json found under {artifacts_dir}/.llm_io/"}

    is_path  = Path(is_files[-1])
    oos_path = Path(oos_files[-1]) if oos_files else None

    is_data  = json.loads(is_path.read_text(encoding="utf-8"))
    oos_data = json.loads(oos_path.read_text(encoding="utf-8")) if oos_path else {}

    loop_num = _extract_loop_num(is_path.name)

    is_metrics  = {k: is_data.get(k)  for k in _REQUIRED_FIELDS}
    oos_metrics = {k: oos_data.get(k) for k in _REQUIRED_FIELDS}

    warnings = []
    is_pf  = is_data.get("profit_factor")  or 0
    oos_pf = oos_data.get("profit_factor") or 0
    is_wr  = is_data.get("win_rate")       or 0
    oos_wr = oos_data.get("win_rate")      or 0
    if is_pf > 0 and oos_pf < is_pf * 0.6:
        warnings.append(f"OOS profit_factor ({oos_pf:.3f}) < IS×0.6 ({is_pf * 0.6:.3f})")
    if is_wr > 0 and oos_wr < is_wr * 0.6:
        warnings.append(f"OOS win_rate ({oos_wr:.3f}) < IS×0.6 ({is_wr * 0.6:.3f})")

    return {
        "mode":                "real",
        "loops_found":         len(is_files),
        f"loop_{loop_num}":    {"IS": is_metrics, "OOS": oos_metrics},
        "overfitting_warnings": warnings,
        "source_files":        {
            "is":  str(is_path),
            "oos": str(oos_path) if oos_path else None,
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract artifact metrics to JSON.")
    parser.add_argument("--mode",          choices=["mock", "real"], required=True)
    parser.add_argument("--artifacts-dir", default="./artifacts")
    parser.add_argument("--output",        required=True)
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    result = extract_mock_metrics(artifacts_dir) if args.mode == "mock" \
             else extract_real_metrics(artifacts_dir)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
