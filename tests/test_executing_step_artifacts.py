"""
tests/test_executing_step_artifacts.py

Unit tests for executing_step helpers covering:

  §6 — summary filename uses analyze_attempt
        (``v0_{analyze_attempt - 1}_summary_report.md``)
  §7 — _upload_new_artifacts skips bundled types (is_zip / oos_zip / etc.)
       and only forwards markdown-class artifacts (revised_direction, audit,
       strategy_spec, ...)

執行方式：
  pytest tests/test_executing_step_artifacts.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# §6 — Summary filename uses analyze_attempt - 1
# ===========================================================================

class TestTerminateSummaryFilename:
    """``_run_terminate_summarize`` must name the report ``v0_{N-1}_summary_report.md``
    where N == analyze_attempt at TERMINATE time."""

    def _run(self, analyze_attempt, tmp_path):
        from app.workflow import executing_step as es

        report_path = tmp_path / "summary.md"
        report_path.write_text("dummy report", encoding="utf-8")

        state = {
            "project_id": "p1",
            "analyze_attempt": analyze_attempt,
            "loop_index": 0,
            "max_loops": 3,
            "last_reason": "terminated by test",
        }

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        terminate_returns = {
            "artifacts": [
                {"type": "terminate_summary", "path": str(report_path)},
            ]
        }

        captured = {}

        def fake_upload(card_id, filename, content):
            captured["filename"] = filename
            captured["card_id"] = card_id

        sink.upload_spec_attachment.side_effect = fake_upload

        post_comments = []

        def fake_post_comment(pid, body):
            post_comments.append(body)

        sink.post_comment.side_effect = fake_post_comment

        with patch.object(es, "terminate_summarize_step", return_value=terminate_returns), \
             patch.object(es, "_merge_state"), \
             patch.object(es, "set_workflow_step"), \
             patch.object(es, "_move"):
            es._run_terminate_summarize("p1", state, db_url=None, sink=sink)

        return captured, post_comments

    def test_attempt_1_yields_v0_0(self, tmp_path):
        captured, comments = self._run(analyze_attempt=1, tmp_path=tmp_path)
        assert captured["filename"] == "v0_0_summary_report.md"
        assert any("v0_0_summary_report.md" in c for c in comments)

    def test_attempt_2_yields_v0_1(self, tmp_path):
        captured, comments = self._run(analyze_attempt=2, tmp_path=tmp_path)
        assert captured["filename"] == "v0_1_summary_report.md"
        assert any("v0_1_summary_report.md" in c for c in comments)

    def test_attempt_3_yields_v0_2(self, tmp_path):
        captured, comments = self._run(analyze_attempt=3, tmp_path=tmp_path)
        assert captured["filename"] == "v0_2_summary_report.md"
        assert any("v0_2_summary_report.md" in c for c in comments)

    def test_attempt_missing_defaults_to_v0_0(self, tmp_path):
        # When analyze_attempt is absent, default to 1 (one iteration ran),
        # so filename is v0_0_summary_report.md.
        from app.workflow import executing_step as es

        report_path = tmp_path / "summary.md"
        report_path.write_text("dummy", encoding="utf-8")

        state = {
            "project_id": "p1",
            "loop_index": 0,
            "max_loops": 3,
            "last_reason": "no attempt set",
        }

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"
        captured = {}
        sink.upload_spec_attachment.side_effect = lambda cid, fn, c: captured.setdefault("filename", fn)

        with patch.object(es, "terminate_summarize_step",
                          return_value={"artifacts": [{"type": "terminate_summary", "path": str(report_path)}]}), \
             patch.object(es, "_merge_state"), \
             patch.object(es, "set_workflow_step"), \
             patch.object(es, "_move"):
            es._run_terminate_summarize("p1", state, db_url=None, sink=sink)

        assert captured["filename"] == "v0_0_summary_report.md"


# ===========================================================================
# §7 — _upload_new_artifacts filters bundled types
# ===========================================================================

class TestUploadNewArtifactsFilter:
    """Bundled artifact types (is_zip, oos_zip, is_result, oos_result, trades,
    signals, report) must be skipped — only markdown-class artifacts pass through."""

    def _make_artifact(self, art_type, path):
        return {"type": art_type, "path": str(path)}

    def _setup_paths(self, tmp_path, type_to_ext):
        """Create files for each given type → suffix mapping."""
        artifacts = []
        for art_type, ext in type_to_ext.items():
            p = tmp_path / f"{art_type}{ext}"
            if ext == ".zip":
                p.write_bytes(b"PK\x03\x04stub")
            else:
                p.write_text(f"# {art_type}\n", encoding="utf-8")
            artifacts.append(self._make_artifact(art_type, p))
        return artifacts

    def test_bundled_types_are_skipped(self, tmp_path):
        from app.workflow import executing_step as es

        artifacts = self._setup_paths(tmp_path, {
            "is_zip": ".zip",
            "oos_zip": ".zip",
            "is_result": ".json",
            "oos_result": ".json",
            "trades": ".json",
            "signals": ".json",
            "report": ".html",
        })

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        es._upload_new_artifacts("p1", artifacts, prev_paths=set(), sink=sink)

        sink.upload_spec_attachment.assert_not_called()
        sink.upload_bytes_attachment.assert_not_called()

    def test_markdown_class_artifacts_pass_through(self, tmp_path):
        from app.workflow import executing_step as es

        artifacts = self._setup_paths(tmp_path, {
            "revised_direction": ".md",
            "audit": ".md",
            "strategy_spec": ".md",
        })

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        es._upload_new_artifacts("p1", artifacts, prev_paths=set(), sink=sink)

        # All three markdown artifacts uploaded via spec attachment
        assert sink.upload_spec_attachment.call_count == 3
        sink.upload_bytes_attachment.assert_not_called()

    def test_mixed_artifacts_only_markdown_uploaded(self, tmp_path):
        from app.workflow import executing_step as es

        # Mix of bundled and markdown
        artifacts = self._setup_paths(tmp_path, {
            "is_zip": ".zip",          # skip
            "oos_zip": ".zip",         # skip
            "report": ".html",         # skip
            "revised_direction": ".md",  # upload
            "audit": ".md",              # upload
        })

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        es._upload_new_artifacts("p1", artifacts, prev_paths=set(), sink=sink)

        assert sink.upload_spec_attachment.call_count == 2
        sink.upload_bytes_attachment.assert_not_called()
        uploaded_names = [call.args[1] for call in sink.upload_spec_attachment.call_args_list]
        assert any("revised_direction" in n for n in uploaded_names)
        assert any("audit" in n for n in uploaded_names)

    def test_prev_paths_skip_still_applies(self, tmp_path):
        """Even non-bundled types must be skipped if path is in prev_paths."""
        from app.workflow import executing_step as es

        p = tmp_path / "audit.md"
        p.write_text("audit", encoding="utf-8")
        artifacts = [{"type": "audit", "path": str(p)}]

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        es._upload_new_artifacts("p1", artifacts, prev_paths={str(p)}, sink=sink)
        sink.upload_spec_attachment.assert_not_called()

    def test_unknown_type_passes_through(self, tmp_path):
        """Unknown / unspecified types are not in the bundled set, so they upload."""
        from app.workflow import executing_step as es

        p = tmp_path / "misc.md"
        p.write_text("misc", encoding="utf-8")
        artifacts = [{"type": "some_new_md_type", "path": str(p)}]

        sink = MagicMock()
        sink.resolve_card_id.return_value = "card-1"

        es._upload_new_artifacts("p1", artifacts, prev_paths=set(), sink=sink)
        sink.upload_spec_attachment.assert_called_once()
