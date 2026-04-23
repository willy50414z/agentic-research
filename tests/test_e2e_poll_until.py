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
