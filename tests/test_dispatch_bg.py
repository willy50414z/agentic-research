"""
tests/test_dispatch_bg.py

Unit tests for ``AppServices.run_dispatch_bg`` max_loops sync timing fix.

Coverage (per spec ``loop-control-sync``):
  - happy path: card_raw=2 -> merged=2 (write to DB before dispatch_step)
  - mismatch warning: merged != parsed -> warning logged but workflow continues
  - missing field fallback: card has no max_loops -> no DB write, no error
  - non-integer fallback: card has "abc" -> exception caught, no DB write

執行方式：
  pytest tests/test_dispatch_bg.py -v
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(planka_client=None, db_url="postgresql://stub"):
    """Build an AppServices instance with stubbed Planka init."""
    from app.api import server

    with patch.object(server, "PLANKA_URL", "http://planka.local"), \
         patch.object(server, "PLANKA_TOKEN", "stub-token"):
        # Bypass __init__'s real PlankaClient construction by short-circuiting.
        svc = server.AppServices.__new__(server.AppServices)
        svc._db_url = db_url
        svc._planka_url = "http://planka.local"
        svc._planka_token = "stub-token"
        svc._planka_board_id = "board-1"
        svc._running = set()
        from threading import Lock
        svc._running_lock = Lock()
        svc._planka_client = planka_client
    return svc


def _project_with_max_loops(value):
    return {"id": "p1", "config": {"max_loops": value}}


# ---------------------------------------------------------------------------
# TC-D01 — happy path: card_raw -> merged -> consistent
# ---------------------------------------------------------------------------

class TestDispatchBgSyncHappyPath:
    def test_card_max_loops_synced_to_db_before_dispatch(self, caplog):
        from app.api import server

        planka = MagicMock()
        planka.read_card_custom_fields.return_value = {"max_loops": "2"}
        svc = _make_svc(planka_client=planka)

        merge_calls = []
        def fake_merge_config(pid, patch_data, db_url):
            merge_calls.append((pid, dict(patch_data), db_url))

        dispatch_calls = []
        def fake_dispatch(pid, **kwargs):
            dispatch_calls.append(pid)

        # get_project must be called *after* merge_config and return the merged value.
        with patch.object(server, "merge_config", side_effect=fake_merge_config), \
             patch.object(server, "get_project", return_value=_project_with_max_loops(2)), \
             patch.object(server, "dispatch_step", side_effect=fake_dispatch), \
             caplog.at_level(logging.INFO, logger="app.api.server"):
            svc.run_dispatch_bg("p1", card_id="card-1")

        # merge_config wrote {"max_loops": 2}
        assert merge_calls == [("p1", {"max_loops": 2}, svc._db_url)]
        # dispatch_step was called after the sync
        assert dispatch_calls == ["p1"]
        # Sync log shows card_raw=2 merged=2
        sync_logs = [r for r in caplog.records if "max_loops sync" in r.message]
        assert sync_logs, "expected max_loops sync log line"
        joined = sync_logs[0].getMessage()
        assert "card_raw=2" in joined
        assert "merged=2" in joined


# ---------------------------------------------------------------------------
# TC-D02 — mismatch triggers warning but does not block dispatch
# ---------------------------------------------------------------------------

class TestDispatchBgMismatchWarning:
    def test_mismatch_logs_warning_but_dispatches(self, caplog):
        from app.api import server

        planka = MagicMock()
        planka.read_card_custom_fields.return_value = {"max_loops": "2"}
        svc = _make_svc(planka_client=planka)

        # Simulate stale read: get_project still returns max_loops=3
        with patch.object(server, "merge_config"), \
             patch.object(server, "get_project", return_value=_project_with_max_loops(3)), \
             patch.object(server, "dispatch_step") as mock_dispatch, \
             caplog.at_level(logging.WARNING, logger="app.api.server"):
            svc.run_dispatch_bg("p1", card_id="card-1")

        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING and "max_loops mismatch" in r.message]
        assert warnings, "expected mismatch warning"
        msg = warnings[0].getMessage()
        assert "card_raw=2" in msg
        assert "merged=3" in msg
        # Dispatch must still happen — no blocking
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# TC-D03 — missing custom field => no DB write, no crash
# ---------------------------------------------------------------------------

class TestDispatchBgMissingField:
    def test_no_max_loops_field_skips_merge(self, caplog):
        from app.api import server

        planka = MagicMock()
        planka.read_card_custom_fields.return_value = {}  # no max_loops
        svc = _make_svc(planka_client=planka)

        with patch.object(server, "merge_config") as mock_merge, \
             patch.object(server, "get_project") as mock_get, \
             patch.object(server, "dispatch_step") as mock_dispatch, \
             caplog.at_level(logging.INFO, logger="app.api.server"):
            svc.run_dispatch_bg("p1", card_id="card-1")

        mock_merge.assert_not_called()
        # get_project should NOT be re-read when there's nothing to merge
        mock_get.assert_not_called()
        mock_dispatch.assert_called_once()
        # The "card_raw=null" log appears
        info_logs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("card_raw=null" in m for m in info_logs)

    def test_empty_string_max_loops_skips_merge(self, caplog):
        from app.api import server

        planka = MagicMock()
        planka.read_card_custom_fields.return_value = {"max_loops": "  "}
        svc = _make_svc(planka_client=planka)

        with patch.object(server, "merge_config") as mock_merge, \
             patch.object(server, "get_project"), \
             patch.object(server, "dispatch_step") as mock_dispatch:
            svc.run_dispatch_bg("p1", card_id="card-1")

        mock_merge.assert_not_called()
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# TC-D04 — non-integer card value => caught, no DB write, dispatch continues
# ---------------------------------------------------------------------------

class TestDispatchBgNonInteger:
    def test_non_integer_value_logs_warning_and_continues(self, caplog):
        from app.api import server

        planka = MagicMock()
        planka.read_card_custom_fields.return_value = {"max_loops": "abc"}
        svc = _make_svc(planka_client=planka)

        with patch.object(server, "merge_config") as mock_merge, \
             patch.object(server, "get_project"), \
             patch.object(server, "dispatch_step") as mock_dispatch, \
             caplog.at_level(logging.WARNING, logger="app.api.server"):
            svc.run_dispatch_bg("p1", card_id="card-1")

        mock_merge.assert_not_called()
        mock_dispatch.assert_called_once()
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING and "max_loops sync failed" in r.message]
        assert warnings, "expected non-integer warning"
