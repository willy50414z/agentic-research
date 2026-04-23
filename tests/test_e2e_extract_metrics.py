"""tests/test_e2e_extract_metrics.py"""
import json
import sys
from pathlib import Path

import pytest

# 讓 Python 能找到 .ai/skills/e2e-test/scripts/
SCRIPTS_DIR = Path(__file__).parent.parent / ".ai" / "skills" / "e2e-test" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class TestExtractMetricsMock:
    def test_mock_returns_correct_fields(self, tmp_path):
        """mock 模式：正確讀取 loop_0_train.json，回傳必要欄位。"""
        from extract_metrics import extract_mock_metrics

        artifact = {
            "loop": 0,
            "plan": {"strategy_name": "TestRsi"},
            "is_result": {
                "win_rate": 0.62,
                "profit_factor": 1.45,
                "max_drawdown": 0.12,
                "n_trades": 47,
                "total_return": 0.21,
                "alpha_ratio": 1.3,
            },
        }
        (tmp_path / "loop_0_train.json").write_text(json.dumps(artifact), encoding="utf-8")

        result = extract_mock_metrics(tmp_path)

        assert result.get("mode") == "mock"
        assert result.get("loops_found") == 1
        loop_data = result.get("loop_0", {})
        assert loop_data["win_rate"] == pytest.approx(0.62)
        assert loop_data["profit_factor"] == pytest.approx(1.45)
        assert loop_data["max_drawdown"] == pytest.approx(0.12)
        assert loop_data["n_trades"] == 47
        assert result.get("missing_fields") == []

    def test_mock_missing_files_returns_error(self, tmp_path):
        """mock 模式：找不到 JSON 時回傳含 error 鍵的 dict，不 raise。"""
        from extract_metrics import extract_mock_metrics

        result = extract_mock_metrics(tmp_path)

        assert "error" in result
        assert "loop_*_train.json" in result["error"]

    def test_mock_picks_latest_when_multiple_loops(self, tmp_path):
        """mock 模式：多個 loop 檔案時取最新（最大 loop 編號）。"""
        from extract_metrics import extract_mock_metrics

        for i in range(3):
            data = {"is_result": {"win_rate": 0.5 + i * 0.05, "profit_factor": 1.0,
                                   "max_drawdown": 0.1, "n_trades": 20}}
            (tmp_path / f"loop_{i}_train.json").write_text(json.dumps(data), encoding="utf-8")

        result = extract_mock_metrics(tmp_path)

        assert result["loops_found"] == 3
        # 最新一筆 loop_2 的 win_rate 應為 0.60
        assert result.get("loop_2", {}).get("win_rate") == pytest.approx(0.60)


class TestExtractMetricsReal:
    def _make_real_fixtures(self, tmp_path, is_pf=1.89, oos_pf=1.34, is_wr=0.62, oos_wr=0.58):
        llm_io = tmp_path / ".llm_io" / "0_20260423_120000"
        llm_io.mkdir(parents=True)
        is_data  = {"win_rate": is_wr,  "profit_factor": is_pf,  "max_drawdown": 0.11, "n_trades": 52}
        oos_data = {"win_rate": oos_wr, "profit_factor": oos_pf, "max_drawdown": 0.14, "n_trades": 23}
        (llm_io / "loop_0_is.json").write_text(json.dumps(is_data),  encoding="utf-8")
        (llm_io / "loop_0_oos.json").write_text(json.dumps(oos_data), encoding="utf-8")
        return tmp_path

    def test_real_returns_is_oos(self, tmp_path):
        """real 模式：回傳 IS 和 OOS 兩組指標。"""
        from extract_metrics import extract_real_metrics

        artifacts_dir = self._make_real_fixtures(tmp_path)
        result = extract_real_metrics(artifacts_dir)

        assert result["mode"] == "real"
        loop_data = result["loop_0"]
        assert loop_data["IS"]["profit_factor"] == pytest.approx(1.89)
        assert loop_data["OOS"]["profit_factor"] == pytest.approx(1.34)
        assert result["overfitting_warnings"] == []

    def test_real_overfitting_warning_when_oos_too_low(self, tmp_path):
        """real 模式：OOS pf < IS * 0.6 時回傳 overfitting_warnings。"""
        from extract_metrics import extract_real_metrics

        # IS pf=2.0, OOS pf=0.5 → 0.5 < 2.0*0.6=1.2 → 警告
        artifacts_dir = self._make_real_fixtures(tmp_path, is_pf=2.0, oos_pf=0.5)
        result = extract_real_metrics(artifacts_dir)

        assert len(result["overfitting_warnings"]) >= 1
        assert "profit_factor" in result["overfitting_warnings"][0]

    def test_real_missing_files_returns_error(self, tmp_path):
        """real 模式：找不到 is.json 時回傳 error dict，不 raise。"""
        from extract_metrics import extract_real_metrics

        result = extract_real_metrics(tmp_path)
        assert "error" in result

    def test_cli_writes_output_file(self, tmp_path):
        """CLI：執行後應寫入 output JSON 檔案。"""
        import subprocess

        artifact = {"is_result": {"win_rate": 0.6, "profit_factor": 1.2,
                                   "max_drawdown": 0.1, "n_trades": 30}}
        (tmp_path / "loop_0_train.json").write_text(json.dumps(artifact), encoding="utf-8")
        output = tmp_path / "out" / "metrics.json"

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "extract_metrics.py"),
                "--mode", "mock",
                "--artifacts-dir", str(tmp_path),
                "--output", str(output),
            ],
            capture_output=True, text=True,
        )

        assert result.returncode == 0, result.stderr
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["mode"] == "mock"
