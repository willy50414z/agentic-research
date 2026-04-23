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
