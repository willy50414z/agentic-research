# E2E Test Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 `.ai/skills/e2e-test/` skill，讓 Claude 透過 `/e2e-test` 指令驅動完整 pipeline 測試並輸出 progress.md。

**Architecture:** 四個 Python 腳本各負責一件事（建卡、polling、metrics 擷取），SKILL.md 編排流程並呼叫這些腳本。所有測試結果寫入 `.ai/e2e-test-runs/<run_id>/progress.md`。測試優先：先寫失敗測試，再實作腳本。

**Tech Stack:** Python 3.11+, httpx, pytest, unittest.mock, python-dotenv；框架內的 `framework.planka.PlankaSink` 用於附件上傳（MinIO 路徑）。

---

## File Map

| 動作 | 路徑 | 說明 |
|------|------|------|
| 建立 | `.ai/skills/e2e-test/SKILL.md` | 完整 skill 工作流程 |
| 建立 | `.ai/skills/e2e-test/scripts/__init__.py` | 空 package init |
| 建立 | `.ai/skills/e2e-test/scripts/setup_card.py` | Phase 2：建卡 + 上傳附件 + 移至 SPR |
| 建立 | `.ai/skills/e2e-test/scripts/poll_until.py` | Phase 3/5：adaptive polling + log 擷取 |
| 建立 | `.ai/skills/e2e-test/scripts/extract_metrics.py` | Phase 6：讀 artifact JSON，輸出統計摘要 |
| 建立 | `tests/test_e2e_extract_metrics.py` | extract_metrics.py 的 pytest 測試 |
| 建立 | `tests/test_e2e_poll_until.py` | poll_until.py 的 pytest 測試 |
| 建立 | `tests/test_e2e_setup_card.py` | setup_card.py 的 pytest 測試 |

---

## Task 1：專案骨架

**Files:**
- 建立：`.ai/skills/e2e-test/scripts/__init__.py`
- 建立：`.ai/skills/e2e-test/scripts/extract_metrics.py`（空 stub）
- 建立：`.ai/skills/e2e-test/scripts/poll_until.py`（空 stub）
- 建立：`.ai/skills/e2e-test/scripts/setup_card.py`（空 stub）

- [ ] **Step 1：建立目錄結構**

```bash
mkdir -p .ai/skills/e2e-test/scripts
```

- [ ] **Step 2：建立 `__init__.py`**

```python
# .ai/skills/e2e-test/scripts/__init__.py
```

（空檔案，讓 scripts/ 成為 Python package）

- [ ] **Step 3：建立三個空 stub 腳本（讓 import 可以通過）**

`.ai/skills/e2e-test/scripts/extract_metrics.py`：
```python
"""extract_metrics.py — 讀取 artifact JSON，輸出統計摘要。"""
```

`.ai/skills/e2e-test/scripts/poll_until.py`：
```python
"""poll_until.py — Adaptive Planka card column poller with log capture."""
```

`.ai/skills/e2e-test/scripts/setup_card.py`：
```python
"""setup_card.py — 建立 Planka 測試卡片，上傳 spec.md，移至 Spec Pending Review。"""
```

- [ ] **Step 4：確認腳本可以 import**

```bash
python -c "import sys; sys.path.insert(0, '.'); \
  from dotenv import load_dotenv; load_dotenv(); \
  exec(open('.ai/skills/e2e-test/scripts/extract_metrics.py').read()); print('OK')"
```

預期輸出：`OK`

- [ ] **Step 5：Commit**

```bash
git add .ai/skills/e2e-test/scripts/
git commit -m "chore: scaffold e2e-test skill script stubs"
```

---

## Task 2：`extract_metrics.py` — mock 模式

**Files:**
- 修改：`.ai/skills/e2e-test/scripts/extract_metrics.py`
- 建立：`tests/test_e2e_extract_metrics.py`

- [ ] **Step 1：寫失敗測試（mock 模式）**

建立 `tests/test_e2e_extract_metrics.py`：

```python
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
```

- [ ] **Step 2：確認測試失敗**

```bash
python -m pytest tests/test_e2e_extract_metrics.py::TestExtractMetricsMock -v 2>&1 | head -30
```

預期：`ImportError` 或 `AttributeError`（函數尚未存在）

- [ ] **Step 3：實作 mock 模式**

將 `.ai/skills/e2e-test/scripts/extract_metrics.py` 改寫為：

```python
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
```

- [ ] **Step 4：確認 mock 測試通過**

```bash
python -m pytest tests/test_e2e_extract_metrics.py::TestExtractMetricsMock -v
```

預期：`3 passed`

---

## Task 3：`extract_metrics.py` — real 模式 + CLI

**Files:**
- 修改：`tests/test_e2e_extract_metrics.py`（新增 class）

- [ ] **Step 1：補寫 real 模式 + CLI 測試**

在 `tests/test_e2e_extract_metrics.py` 末尾加入：

```python
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
```

- [ ] **Step 2：確認新測試失敗（real 模式函數已在 Task 2 實作，應全部通過）**

```bash
python -m pytest tests/test_e2e_extract_metrics.py -v
```

預期：`7 passed`

- [ ] **Step 3：Commit**

```bash
git add .ai/skills/e2e-test/scripts/extract_metrics.py tests/test_e2e_extract_metrics.py
git commit -m "feat: implement extract_metrics.py with mock/real modes and tests"
```

---

## Task 4：`poll_until.py` — Planka API helpers

**Files:**
- 修改：`.ai/skills/e2e-test/scripts/poll_until.py`
- 建立：`tests/test_e2e_poll_until.py`

- [ ] **Step 1：寫失敗測試（API helpers）**

建立 `tests/test_e2e_poll_until.py`：

```python
"""tests/test_e2e_poll_until.py"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / ".ai" / "skills" / "e2e-test" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class TestGetCardColumn:
    def _make_mock_responses(self, list_id="list-abc", list_name="Verify"):
        card_resp = MagicMock()
        card_resp.raise_for_status = MagicMock()
        card_resp.json.return_value = {"item": {"listId": list_id}}

        board_resp = MagicMock()
        board_resp.raise_for_status = MagicMock()
        board_resp.json.return_value = {
            "included": {"lists": [{"id": list_id, "name": list_name}]}
        }
        return card_resp, board_resp

    def test_returns_column_name(self):
        """get_card_column: 正確解析 listId 並對照 board lists 回傳 column 名稱。"""
        from poll_until import get_card_column

        card_resp, board_resp = self._make_mock_responses("list-abc", "Verify")
        with patch("httpx.get", side_effect=[card_resp, board_resp]):
            result = get_card_column("card-1", "http://planka", "token", "board-1")

        assert result == "Verify"

    def test_returns_none_on_http_error(self):
        """get_card_column: HTTP 錯誤時回傳 None，不 raise。"""
        from poll_until import get_card_column

        with patch("httpx.get", side_effect=Exception("connection refused")):
            result = get_card_column("card-1", "http://planka", "token", "board-1")

        assert result is None

    def test_returns_none_when_list_not_found(self):
        """get_card_column: listId 不在 board lists 中時回傳 None。"""
        from poll_until import get_card_column

        card_resp = MagicMock()
        card_resp.raise_for_status = MagicMock()
        card_resp.json.return_value = {"item": {"listId": "unknown-list"}}

        board_resp = MagicMock()
        board_resp.raise_for_status = MagicMock()
        board_resp.json.return_value = {
            "included": {"lists": [{"id": "list-xyz", "name": "Planning"}]}
        }

        with patch("httpx.get", side_effect=[card_resp, board_resp]):
            result = get_card_column("card-1", "http://planka", "token", "board-1")

        assert result is None


class TestCaptureLogs:
    def test_docker_source_filters_by_grep(self, tmp_path):
        """capture_logs: docker source — 只保留符合 grep pattern 的行。"""
        from poll_until import capture_logs

        docker_output = "\n".join([
            "2026-04-23 [INFO] [NODE ENTER] PLAN  project=abc",
            "2026-04-23 [INFO] some unrelated log line",
            "2026-04-23 [INFO] [NODE EXIT]  PLAN  project=abc",
        ])
        mock_result = MagicMock()
        mock_result.stdout = docker_output
        mock_result.stderr = ""

        output_path = str(tmp_path / "out.log")
        with patch("subprocess.run", return_value=mock_result):
            count = capture_logs("docker:my-container", r"NODE (ENTER|EXIT)", output_path)

        assert count == 2
        content = Path(output_path).read_text(encoding="utf-8")
        assert "NODE ENTER" in content
        assert "unrelated" not in content

    def test_file_source_filters_by_grep(self, tmp_path):
        """capture_logs: file source — 讀檔並過濾。"""
        from poll_until import capture_logs

        log_file = tmp_path / "server.log"
        log_file.write_text(
            "[SPEC-REVIEW] START\nsome noise\n[SPEC-REVIEW] ROUND 1/2\n",
            encoding="utf-8",
        )
        output_path = str(tmp_path / "out.log")

        count = capture_logs(f"file:{log_file}", r"\[SPEC-REVIEW\]", output_path)

        assert count == 2

    def test_empty_source_returns_zero(self, tmp_path):
        """capture_logs: LOG_SOURCE 為空字串時回傳 0，不寫檔。"""
        from poll_until import capture_logs

        output_path = str(tmp_path / "out.log")
        count = capture_logs("", r".*", output_path)

        assert count == 0
        assert not Path(output_path).exists()
```

- [ ] **Step 2：確認測試失敗**

```bash
python -m pytest tests/test_e2e_poll_until.py::TestGetCardColumn \
                 tests/test_e2e_poll_until.py::TestCaptureLogs -v 2>&1 | head -20
```

預期：`ImportError` 或 `AttributeError`

- [ ] **Step 3：實作 helpers**

將 `.ai/skills/e2e-test/scripts/poll_until.py` 改寫為（helpers 部分，main 下一個 task）：

```python
"""poll_until.py — Adaptive Planka card column poller with log capture.

Usage:
    python poll_until.py \
        --card-id <id> \
        --target-columns "Verify,Planning" \
        --timeout 900 \
        --interval-early 30 \
        --interval-late 120 \
        --early-window 300 \
        --log-source "docker:agentic-framework-api" \
        --log-grep "SPEC.REVIEW" \
        --log-output /path/to/logs/spec-review.log

Outputs JSON to stdout:
    {"status": "reached", "column": "Verify", "elapsed_seconds": 312, "log_lines": 45, "error": null}
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx


# ── Planka API helpers ────────────────────────────────────────────────────────

def get_card_column(card_id: str, planka_url: str, token: str, board_id: str) -> str | None:
    """GET card → listId → board lists → column name. Returns None on any error."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        card_resp = httpx.get(f"{planka_url}/api/cards/{card_id}", headers=headers, timeout=10)
        card_resp.raise_for_status()
        list_id = card_resp.json()["item"]["listId"]

        board_resp = httpx.get(f"{planka_url}/api/boards/{board_id}", headers=headers, timeout=10)
        board_resp.raise_for_status()
        lists = board_resp.json().get("included", {}).get("lists", [])
        for lst in lists:
            if lst.get("id") == list_id:
                return lst.get("name")
    except Exception as e:
        print(f"[poll_until] get_card_column error: {e}", file=sys.stderr)
    return None


# ── Log capture ───────────────────────────────────────────────────────────────

def capture_logs(log_source: str, grep_pattern: str, output_path: str) -> int:
    """Capture and filter logs. Returns number of matching lines saved."""
    if not log_source:
        return 0

    lines: list[str] = []
    try:
        if log_source.startswith("file:"):
            path = Path(log_source[5:])
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                lines = [l for l in content.splitlines() if re.search(grep_pattern, l)]
        elif log_source.startswith("docker:"):
            container = log_source[7:]
            proc = subprocess.run(
                ["docker", "logs", container, "--tail", "500"],
                capture_output=True, text=True, timeout=15,
            )
            all_lines = proc.stdout.splitlines() + proc.stderr.splitlines()
            lines = [l for l in all_lines if re.search(grep_pattern, l)]
    except Exception as e:
        print(f"[poll_until] log capture error: {e}", file=sys.stderr)
        return 0

    if lines and output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(lines[-500:]), encoding="utf-8")

    return len(lines)


# ── Placeholder for main() — implemented in Task 5 ───────────────────────────

def main():
    raise NotImplementedError("main() implemented in Task 5")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4：確認 helpers 測試通過**

```bash
python -m pytest tests/test_e2e_poll_until.py::TestGetCardColumn \
                 tests/test_e2e_poll_until.py::TestCaptureLogs -v
```

預期：`6 passed`

---

## Task 5：`poll_until.py` — main polling loop

**Files:**
- 修改：`.ai/skills/e2e-test/scripts/poll_until.py`（補上 `main()`）
- 修改：`tests/test_e2e_poll_until.py`（新增 `TestPollingLoop`）

- [ ] **Step 1：寫失敗測試（polling loop）**

在 `tests/test_e2e_poll_until.py` 末尾加入：

```python
class TestPollingLoop:
    """測試 poll_until.py 的主 polling 邏輯。"""

    def _run_poll_until(self, argv: list[str], env: dict = None) -> dict:
        """執行 poll_until.py 並解析 stdout JSON。"""
        import os
        combined_env = {**os.environ, **(env or {})}
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "poll_until.py")] + argv,
            capture_output=True, text=True, env=combined_env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        return json.loads(result.stdout.strip())

    def test_returns_reached_when_column_matches(self, tmp_path):
        """已在目標 column 時，立即回傳 status=reached。"""
        # 用 monkeypatch 替換 httpx → 直接回傳 "Verify"
        # 因為跨 subprocess，改用 mock server — 這裡用 socket trick
        # 簡化：直接測試 _main_logic 函數（不走 subprocess）
        from poll_until import _main_logic

        call_count = {"n": 0}

        def fake_get_column(card_id, url, token, board_id):
            call_count["n"] += 1
            return "Verify"

        out = _main_logic(
            card_id="c1",
            target_columns={"Verify", "Planning"},
            timeout=30,
            interval_early=5,
            interval_late=10,
            early_window=15,
            log_source="",
            log_grep=".",
            log_output="",
            planka_url="http://mock",
            token="t",
            board_id="b",
            _get_column_fn=fake_get_column,
            _sleep_fn=lambda _: None,
            _time_fn=lambda: 0.0,
        )

        assert out["status"] == "reached"
        assert out["column"] == "Verify"
        assert call_count["n"] == 1

    def test_returns_timeout_when_column_never_matches(self, tmp_path):
        """column 從不匹配時，到達 timeout 後回傳 status=timeout。"""
        from poll_until import _main_logic

        tick = {"t": 0.0}

        def fake_time():
            return tick["t"]

        def fake_sleep(seconds):
            tick["t"] += seconds

        def fake_get_column(*_):
            return "Planning"  # 永遠不是目標

        out = _main_logic(
            card_id="c1",
            target_columns={"Verify"},
            timeout=60,
            interval_early=30,
            interval_late=60,
            early_window=30,
            log_source="",
            log_grep=".",
            log_output="",
            planka_url="http://mock",
            token="t",
            board_id="b",
            _get_column_fn=fake_get_column,
            _sleep_fn=fake_sleep,
            _time_fn=fake_time,
        )

        assert out["status"] == "timeout"
        assert out["column"] == "Planning"

    def test_adaptive_interval_switches_after_early_window(self, tmp_path):
        """前段用 interval_early，超過 early_window 後用 interval_late。"""
        from poll_until import _main_logic

        tick = {"t": 0.0}
        sleep_calls = []

        def fake_time():
            return tick["t"]

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            tick["t"] += seconds  # advance time

        call_count = {"n": 0}

        def fake_get_column(*_):
            call_count["n"] += 1
            # 前兩次 polling 仍在 early_window（0s, 30s）
            # 第三次已超過 early_window（60s > 50s）
            # 第四次才到目標
            if call_count["n"] >= 4:
                return "Verify"
            return "Spec Pending Review"

        _main_logic(
            card_id="c1",
            target_columns={"Verify"},
            timeout=300,
            interval_early=30,
            interval_late=120,
            early_window=50,   # 50 秒後切換到 late interval
            log_source="",
            log_grep=".",
            log_output="",
            planka_url="http://mock",
            token="t",
            board_id="b",
            _get_column_fn=fake_get_column,
            _sleep_fn=fake_sleep,
            _time_fn=fake_time,
        )

        # 前兩次 sleep 應為 interval_early=30，之後應為 interval_late=120
        assert sleep_calls[0] == 30
        assert sleep_calls[1] == 30
        assert sleep_calls[2] == 120
```

- [ ] **Step 2：確認測試失敗**

```bash
python -m pytest tests/test_e2e_poll_until.py::TestPollingLoop -v 2>&1 | head -20
```

預期：`ImportError: cannot import name '_main_logic'`

- [ ] **Step 3：實作 `_main_logic` 與 `main()`**

在 `poll_until.py` 的 `main()` 前插入以下程式碼（取代原有 placeholder）：

```python
# ── Main logic (injectable for testing) ──────────────────────────────────────

def _main_logic(
    card_id: str,
    target_columns: set[str],
    timeout: int,
    interval_early: int,
    interval_late: int,
    early_window: int,
    log_source: str,
    log_grep: str,
    log_output: str,
    planka_url: str,
    token: str,
    board_id: str,
    _get_column_fn=None,
    _sleep_fn=None,
    _time_fn=None,
) -> dict:
    """
    Core polling loop. Dependencies injectable for unit tests.
    Returns dict: {status, column, elapsed_seconds, log_lines, error}
    """
    _get_col = _get_column_fn or (lambda cid, url, tok, bid: get_card_column(cid, url, tok, bid))
    _sleep   = _sleep_fn or time.sleep
    _now     = _time_fn  or time.time

    start = _now()

    while True:
        elapsed = _now() - start
        column  = _get_col(card_id, planka_url, token, board_id)

        if column in target_columns:
            log_lines = capture_logs(log_source, log_grep, log_output)
            return {
                "status":          "reached",
                "column":          column,
                "elapsed_seconds": int(elapsed),
                "log_lines":       log_lines,
                "error":           None,
            }

        if elapsed >= timeout:
            log_lines = capture_logs(log_source, log_grep, log_output)
            return {
                "status":          "timeout",
                "column":          column,
                "elapsed_seconds": int(elapsed),
                "log_lines":       log_lines,
                "error":           f"Timeout after {timeout}s. Last column: {column}",
            }

        interval = interval_early if elapsed < early_window else interval_late
        _sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Poll Planka card until target column is reached.")
    parser.add_argument("--card-id",        required=True)
    parser.add_argument("--target-columns", required=True,
                        help="Comma-separated column names, e.g. 'Verify,Planning'")
    parser.add_argument("--timeout",        type=int, default=900)
    parser.add_argument("--interval-early", type=int, default=30)
    parser.add_argument("--interval-late",  type=int, default=120)
    parser.add_argument("--early-window",   type=int, default=300)
    parser.add_argument("--log-source",     default="")
    parser.add_argument("--log-grep",       default=".")
    parser.add_argument("--log-output",     default="")
    args = parser.parse_args()

    planka_url = os.getenv("PLANKA_API_URL", "").rstrip("/")
    token      = os.getenv("PLANKA_TOKEN", "")
    board_id   = os.getenv("PLANKA_BOARD_ID", "")

    result = _main_logic(
        card_id        = args.card_id,
        target_columns = {c.strip() for c in args.target_columns.split(",")},
        timeout        = args.timeout,
        interval_early = args.interval_early,
        interval_late  = args.interval_late,
        early_window   = args.early_window,
        log_source     = args.log_source,
        log_grep       = args.log_grep,
        log_output     = args.log_output,
        planka_url     = planka_url,
        token          = token,
        board_id       = board_id,
    )
    print(json.dumps(result))
```

- [ ] **Step 4：確認所有 poll_until 測試通過**

```bash
python -m pytest tests/test_e2e_poll_until.py -v
```

預期：`9 passed`

- [ ] **Step 5：Commit**

```bash
git add .ai/skills/e2e-test/scripts/poll_until.py tests/test_e2e_poll_until.py
git commit -m "feat: implement poll_until.py with adaptive polling and injectable dependencies"
```

---

## Task 6：`setup_card.py`

**Files:**
- 修改：`.ai/skills/e2e-test/scripts/setup_card.py`
- 建立：`tests/test_e2e_setup_card.py`

- [ ] **Step 1：寫失敗測試**

建立 `tests/test_e2e_setup_card.py`：

```python
"""tests/test_e2e_setup_card.py"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / ".ai" / "skills" / "e2e-test" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _mock_httpx_sequence(responses: list) -> MagicMock:
    """回傳依序回應的 httpx.get/post/patch mock。"""
    mock = MagicMock()
    mock.side_effect = responses
    return mock


class TestSetupCard:
    def _make_board_response(self, planning_id="list-plan", spr_id="list-spr"):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "included": {
                "lists": [
                    {"id": planning_id, "name": "Planning"},
                    {"id": spr_id,      "name": "Spec Pending Review"},
                ]
            }
        }
        return resp

    def _make_card_create_response(self, card_id="card-xyz"):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"item": {"id": card_id}}
        return resp

    def test_returns_card_id_and_thread_id(self, tmp_path):
        """setup_card: 成功建立卡片時回傳含 card_id 與 thread_id 的 dict。"""
        from setup_card import setup_card

        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Test Strategy", encoding="utf-8")

        board_resp  = self._make_board_response()
        create_resp = self._make_card_create_response("card-xyz")
        patch_resp  = MagicMock(); patch_resp.raise_for_status = MagicMock()
        attach_resp = MagicMock()
        attach_resp.raise_for_status = MagicMock()
        attach_resp.status_code = 200
        move_resp   = MagicMock(); move_resp.raise_for_status = MagicMock()

        with patch("httpx.get",   return_value=board_resp), \
             patch("httpx.post",  side_effect=[create_resp, attach_resp]), \
             patch("httpx.patch", side_effect=[patch_resp, move_resp]):
            result = setup_card(
                planka_url="http://planka",
                token="tok",
                board_id="board-1",
                spec_path=str(spec_file),
                run_id="20260423-143000",
            )

        assert result["card_id"] == "card-xyz"
        assert "thread_id" in result
        assert result["thread_id"].startswith("e2e-test-")
        assert result["error"] is None

    def test_returns_error_when_board_fetch_fails(self, tmp_path):
        """setup_card: board API 失敗時回傳 error dict，不 raise。"""
        from setup_card import setup_card

        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Test", encoding="utf-8")

        with patch("httpx.get", side_effect=Exception("connection refused")):
            result = setup_card(
                planka_url="http://planka",
                token="tok",
                board_id="board-1",
                spec_path=str(spec_file),
                run_id="20260423-143000",
            )

        assert result["error"] is not None
        assert "connection refused" in result["error"]

    def test_returns_error_when_spec_file_missing(self, tmp_path):
        """setup_card: spec 檔案不存在時回傳 error dict。"""
        from setup_card import setup_card

        result = setup_card(
            planka_url="http://planka",
            token="tok",
            board_id="board-1",
            spec_path=str(tmp_path / "nonexistent.md"),
            run_id="20260423-143000",
        )

        assert result["error"] is not None
        assert "spec" in result["error"].lower()
```

- [ ] **Step 2：確認測試失敗**

```bash
python -m pytest tests/test_e2e_setup_card.py -v 2>&1 | head -20
```

預期：`ImportError`

- [ ] **Step 3：實作 `setup_card.py`**

```python
"""setup_card.py — 建立 Planka 測試卡片，上傳 spec.md，移至 Spec Pending Review。

Usage:
    python setup_card.py \
        --spec-path tests/spec.md \
        --run-id 20260423-143000

Outputs JSON to stdout:
    {"card_id": "xxx", "thread_id": "e2e-test-143000", "error": null}

環境變數（從 .env 讀取）：
    PLANKA_API_URL, PLANKA_TOKEN, PLANKA_BOARD_ID
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx


def setup_card(
    planka_url: str,
    token: str,
    board_id: str,
    spec_path: str,
    run_id: str,
) -> dict:
    """
    建立測試卡片、上傳 spec.md、移至 Spec Pending Review。
    回傳 {"card_id": str, "thread_id": str, "error": str | None}。
    """
    headers = {"Authorization": f"Bearer {token}"}
    thread_id = f"e2e-test-{run_id.split('-')[-1]}"  # e.g. e2e-test-143000

    # ── 驗證 spec 檔案存在 ──────────────────────────────────────────────────
    spec = Path(spec_path)
    if not spec.exists():
        return {"card_id": None, "thread_id": thread_id,
                "error": f"spec file not found: {spec_path}"}

    try:
        # ── 取得 Planning + Spec Pending Review 的 list_id ─────────────────
        board_resp = httpx.get(
            f"{planka_url}/api/boards/{board_id}", headers=headers, timeout=10
        )
        board_resp.raise_for_status()
        lists = board_resp.json().get("included", {}).get("lists", [])
        list_map = {lst["name"]: lst["id"] for lst in lists}

        planning_id = list_map.get("Planning")
        spr_id      = list_map.get("Spec Pending Review")
        if not planning_id or not spr_id:
            return {"card_id": None, "thread_id": thread_id,
                    "error": f"Required columns not found. Available: {list(list_map.keys())}"}

        # ── 建立卡片（在 Planning）──────────────────────────────────────────
        card_resp = httpx.post(
            f"{planka_url}/api/lists/{planning_id}/cards",
            headers=headers,
            json={"name": f"[E2E Test] Turtle Trading {run_id}", "position": 65535},
            timeout=10,
        )
        card_resp.raise_for_status()
        card_id = card_resp.json()["item"]["id"]

        # ── 注入 thread_id 到 description ─────────────────────────────────
        httpx.patch(
            f"{planka_url}/api/cards/{card_id}",
            headers=headers,
            json={"description": f"thread_id: {thread_id}\n\n[E2E automated test run {run_id}]"},
            timeout=10,
        ).raise_for_status()

        # ── 上傳 spec.md 附件 ───────────────────────────────────────────────
        spec_content = spec.read_bytes()
        attach_resp = httpx.post(
            f"{planka_url}/api/cards/{card_id}/attachments",
            headers=headers,
            files={"file": ("spec.md", spec_content, "text/markdown")},
            timeout=30,
        )
        attach_resp.raise_for_status()

        # ── 移動卡片至 Spec Pending Review（觸發 webhook）────────────────
        httpx.patch(
            f"{planka_url}/api/cards/{card_id}",
            headers=headers,
            json={"listId": spr_id, "position": 65535},
            timeout=10,
        ).raise_for_status()

        return {"card_id": card_id, "thread_id": thread_id, "error": None}

    except Exception as e:
        return {"card_id": None, "thread_id": thread_id, "error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-path", required=True)
    parser.add_argument("--run-id",    required=True)
    args = parser.parse_args()

    result = setup_card(
        planka_url = os.getenv("PLANKA_API_URL", "").rstrip("/"),
        token      = os.getenv("PLANKA_TOKEN", ""),
        board_id   = os.getenv("PLANKA_BOARD_ID", ""),
        spec_path  = args.spec_path,
        run_id     = args.run_id,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4：確認測試通過**

```bash
python -m pytest tests/test_e2e_setup_card.py -v
```

預期：`3 passed`

- [ ] **Step 5：Commit**

```bash
git add .ai/skills/e2e-test/scripts/setup_card.py tests/test_e2e_setup_card.py
git commit -m "feat: implement setup_card.py with Planka card creation and spec upload"
```

---

## Task 7：`SKILL.md`

**Files:**
- 建立：`.ai/skills/e2e-test/SKILL.md`

- [ ] **Step 1：建立 SKILL.md**

```markdown
---
name: e2e-test
description: >
  執行 agentic-research 框架端對端整合測試。從建立 Planka 卡片開始，驅動完整
  pipeline（Spec Review → Research Graph）至第一輪結束，嚴格驗證每個里程碑，
  並將全部過程記錄在 progress.md 供 review。
  觸發時機：使用者輸入 /e2e-test，或要求執行端對端測試、整合測試、pipeline 驗證時。
---

# E2E Integration Test

## 前置需求確認

從 `.env` 讀取並驗證以下變數存在（缺少任一則 abort）：
- `PLANKA_API_URL`、`PLANKA_TOKEN`、`PLANKA_BOARD_ID`
- `DATABASE_URL`
- `BACKTEST_MODE`（預設 `mock`）
- `LOG_SOURCE`（選填）

## 執行步驟

### Phase 1 — 環境確認

1. 執行 `docker compose ps` 確認 `agentic-research-postgres`、`agentic-planka`、
   `agentic-minio` 均 healthy。任一不健康則 abort。

2. 呼叫 `GET http://localhost:8002/health`：
   - 若 200 → 繼續
   - 若失敗 → 背景啟動 `python main.py`（`run_in_background=True`），
     每 5 秒 poll `/health`，最多等 30 秒，仍失敗則 abort

3. 呼叫 `GET http://localhost:8002/health/llm`，記錄 provider 狀態

4. 建立 run 目錄與 progress.md 骨架：
   ```python
   from datetime import datetime
   run_id  = datetime.now().strftime("%Y%m%d-%H%M%S")
   run_dir = f".ai/e2e-test-runs/{run_id}"
   # mkdir run_dir/logs/
   # 寫入 progress.md skeleton（見「Progress.md 範本」段落）
   ```

### Phase 2 — 建立測試卡片

5. 執行 `setup_card.py`：
   ```bash
   python .ai/skills/e2e-test/scripts/setup_card.py \
     --spec-path tests/spec.md \
     --run-id {run_id}
   ```
   解析 JSON 輸出，取得 `card_id`、`thread_id`。
   `error` 非 null → abort，記錄錯誤到 progress.md。

6. 更新 progress.md Phase 2 checklist（card_id、thread_id 實際值）

### Phase 3 — Spec Review 監測

7. 記錄開始時間戳
8. 執行 `poll_until.py`（timeout 15 分鐘）：
   ```bash
   python .ai/skills/e2e-test/scripts/poll_until.py \
     --card-id {card_id} \
     --target-columns "Verify,Planning" \
     --timeout 900 \
     --interval-early 30 \
     --interval-late 60 \
     --early-window 300 \
     --log-source "{LOG_SOURCE}" \
     --log-grep "SPEC.REVIEW|spec_review|NODE ENTER.*SPEC" \
     --log-output {run_dir}/logs/spec-review.log
   ```
9. 解析 JSON 輸出，更新 progress.md Phase 3（等待時間、最終 column）

### Phase 4 — Spec Review 斷言

對每個斷言項目，更新 progress.md 為 ✅/❌/⏭ + 實際值：

10. **4-1** 確認 column 為 `Verify`（非 `Planning`）：
    ```python
    import httpx, os
    headers = {"Authorization": f"Bearer {os.getenv('PLANKA_TOKEN')}"}
    card    = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}", headers=headers).json()
    list_id = card["item"]["listId"]
    board   = httpx.get(f"{PLANKA_URL}/api/boards/{BOARD_ID}", headers=headers).json()
    column  = next((l["name"] for l in board["included"]["lists"] if l["id"] == list_id), None)
    ```

11. **4-2** 確認任一 comment 含 `[SPEC-REVIEW] PASS`：
    ```python
    actions  = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}/actions", headers=headers).json()
    comments = [a["data"]["text"] for a in actions.get("items", [])
                if a.get("type") == "commentCard"]
    pass_comment = next((c for c in comments if "[SPEC-REVIEW] PASS" in c), None)
    ```

12. **4-3** 確認任一 comment 含 `plugin: quant_alpha`

13. **4-4/4-5** 確認附件含 `reviewed_spec_initial.md` 與 `reviewed_spec_final.md`：
    ```python
    card_detail  = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}", headers=headers).json()
    attachments  = card_detail.get("included", {}).get("attachments", [])
    attach_names = [a["name"] for a in attachments]
    ```

14. **4-6** 若 `LOG_SOURCE` 已設定，確認 `spec-review.log` 含 `[NODE ENTER] SPEC_REVIEW_INIT`

### Phase 5 — Research Graph 監測

15. 確認卡片在 Verify（spec review 已把卡片移過去，webhook 應自動觸發）
16. 記錄開始時間戳
17. 執行 `poll_until.py`（adaptive，timeout 30 分鐘）：
    ```bash
    python .ai/skills/e2e-test/scripts/poll_until.py \
      --card-id {card_id} \
      --target-columns "Done,Failed,Review" \
      --timeout 1800 \
      --interval-early 30 \
      --interval-late 120 \
      --early-window 300 \
      --log-source "{LOG_SOURCE}" \
      --log-grep "NODE ENTER|NODE EXIT|ROUTE|QuantAlpha" \
      --log-output {run_dir}/logs/research.log
    ```
18. 解析輸出，更新 progress.md Phase 5

### Phase 6 — Research 斷言 + 最終報告

19. **6-1** 確認最終 column 在 `Done`/`Failed`/`Review`（記錄實際值）
20. **6-2** 確認任一 comment 含 `last_result=`（loop metrics）
21. **6-3** 確認附件名稱符合 `v*_researchsummary_*.md` pattern
22. **6-4** 若有 log，確認含 `[NODE ENTER] PLAN`、`IMPLEMENT`、`TEST`、`ANALYZE`

23. 執行 `extract_metrics.py`：
    ```bash
    python .ai/skills/e2e-test/scripts/extract_metrics.py \
      --mode {BACKTEST_MODE} \
      --artifacts-dir ./artifacts \
      --output {run_dir}/metrics_summary.json
    ```
24. 讀取 `metrics_summary.json`，格式化為 Markdown 表格，插入 progress.md

25. 計算通過率（✅ 數 / 總 checklist 數），寫入最終結果區塊

26. 輸出：
    ```
    ✅ E2E Test 完成。結果：{PASS/FAIL}（{n}/{m} 通過）
    Progress report: {run_dir}/progress.md
    ```

## Progress.md 範本

```markdown
# E2E Test Run — {YYYY-MM-DD HH:MM:SS}

## 環境
- run_id: {run_id}
- thread_id: （待填入）
- card_id: （待填入）
- BACKTEST_MODE: {mock|real}
- LOG_SOURCE: {value|未設定}
- API: http://localhost:8002
- Planka: {PLANKA_API_URL}

## Phase 1 — 前置確認
- [ ] postgres healthy
- [ ] planka healthy
- [ ] minio healthy
- [ ] API /health 200
- [ ] API /health/llm — providers: （待填）

## Phase 2 — Setup
- [ ] 卡片建立 — card_id: （待填）
- [ ] spec.md 上傳成功
- [ ] 卡片移至 Spec Pending Review

## Phase 3 — Spec Review 監測
- 等待時間: —
- 最終 column: —

## Phase 4 — Spec Review 斷言
- [ ] 4-1 卡片在 Verify column
- [ ] 4-2 [SPEC-REVIEW] PASS comment 存在
- [ ] 4-3 plugin: quant_alpha
- [ ] 4-4 附件 reviewed_spec_initial.md
- [ ] 4-5 附件 reviewed_spec_final.md
- [ ] 4-6 Log 含 SPEC_REVIEW_INIT

## Phase 5 — Research 監測
- 等待時間: —
- 最終 column: —

## Phase 6 — Research 斷言
- [ ] 6-1 最終 column 在預期範圍
- [ ] 6-2 loop metrics comment 存在
- [ ] 6-3 researchsummary 附件存在
- [ ] 6-4 Log 含關鍵節點

## Artifact 統計
（由 extract_metrics.py 填入）

## 擷取的 Log 片段
### Spec Review
（來自 logs/spec-review.log）

### Research Graph
（來自 logs/research.log）

## 最終結果
**整體判定**：（PASS / FAIL）
**通過率**：— / —
**耗時**：—
**失敗項目**：
```

## 錯誤處理原則

- Phase 1 任何步驟失敗 → 記錄原因到 progress.md，abort（不繼續）
- Phase 2-6 斷言失敗 → 記錄 ❌ + 實際值，繼續執行（不 abort）
- poll_until.py timeout → 記錄 TIMEOUT，繼續後續斷言
- API 呼叫失敗 → 記錄錯誤訊息，斷言標記 ❌
- `extract_metrics.py` 回傳 error 鍵 → 記錄警告，不影響整體 PASS/FAIL 判定
```

- [ ] **Step 2：確認 SKILL.md 所有 script 路徑與 Task 1–6 實作的路徑一致**

手動核對清單：
- `setup_card.py` 路徑：`.ai/skills/e2e-test/scripts/setup_card.py` ✓
- `poll_until.py` 路徑：`.ai/skills/e2e-test/scripts/poll_until.py` ✓
- `extract_metrics.py` 路徑：`.ai/skills/e2e-test/scripts/extract_metrics.py` ✓
- Planka API endpoints 與 `framework/planka.py` 使用的一致 ✓

- [ ] **Step 3：Commit**

```bash
git add .ai/skills/e2e-test/SKILL.md
git commit -m "feat: add e2e-test SKILL.md with full workflow orchestration"
```

---

## Task 8：全套測試 + 煙霧測試

**Files:** 無新檔案

- [ ] **Step 1：執行所有 e2e skill 測試**

```bash
python -m pytest tests/test_e2e_extract_metrics.py \
                 tests/test_e2e_poll_until.py \
                 tests/test_e2e_setup_card.py -v
```

預期：`19 passed`（7 + 9 + 3）

- [ ] **Step 2：確認腳本 CLI help 正常**

```bash
python .ai/skills/e2e-test/scripts/extract_metrics.py --help
python .ai/skills/e2e-test/scripts/poll_until.py --help
python .ai/skills/e2e-test/scripts/setup_card.py --help
```

三個指令均應印出 usage，exit code 0。

- [ ] **Step 3：確認既有測試未被破壞**

```bash
python -m pytest tests/ -v --ignore=tests/test_freqtrade_integration.py -q
```

預期：原有測試全數通過，無新的 failure。

- [ ] **Step 4：最終 Commit**

```bash
git add .
git commit -m "feat: complete e2e-test skill — scripts, tests, SKILL.md"
```

---

## Self-Review

**Spec 覆蓋確認：**

| Spec 需求 | 對應 Task |
|-----------|----------|
| 建立測試進度文件 | Task 7（SKILL.md Phase 1 step 4） |
| 建立 Planka 卡片 | Task 6（setup_card.py） |
| 上傳策略 md（tests/README.md） | Task 6（setup_card.py） |
| 監測調用進度（Planka + log） | Task 4/5（poll_until.py） |
| 更新測試進度 | Task 7（SKILL.md 每 phase 後更新） |
| Spec Review 嚴格斷言 | Task 7（Phase 4 斷言） |
| Research 嚴格斷言 | Task 7（Phase 6 斷言） |
| 透過 code 擷取統計資料 | Task 2/3（extract_metrics.py） |
| BACKTEST_MODE 感知 | Task 2/3（mock/real 分支） |
| 支援 mock + real 兩模式 | Task 2/3 |
| adaptive polling | Task 5（`_main_logic` early/late window） |
| 啟動 framework API | Task 7（SKILL.md Phase 1 step 2） |
| 觸發 prompt `/e2e-test` | Task 7（SKILL.md frontmatter description） |

**無 placeholder。所有型別與函數名稱在各 Task 間一致。**
