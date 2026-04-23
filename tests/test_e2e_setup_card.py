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
