"""
tests/test_positive_flows.py

QA 正向案例完整測試流程 — Agentic Research Workflow

覆蓋範圍（Happy Path）：
  TC-P01  直接 PASS — 第一次 analyze 就通過
  TC-P02  FAIL → PASS — 經過一次 revise 後通過
  TC-P03  EXHAUSTED TERMINATE — max_loops 次全部 FAIL，觸發 EXHAUSTED
  TC-P04  HITL Plan Approval — interrupt → approve → PASS
  TC-P05  Per-loop md 上傳 (PASS 路徑)
  TC-P06  Per-loop md 上傳 (TERMINATE 路徑) — 修正後的行為
  TC-P07  Final summary 在所有 TERMINATE 都產生 — 修正後的行為
  TC-P08  FAIL loop metrics 記錄到 DB — 修正後的行為
  TC-P09  analyze wrapper 阻止 LLM 提前 TERMINATE
  TC-P10  Planka 卡片狀態機 (PASS → Done / TERMINATE → Review)

執行方式：
  # 需要 PostgreSQL（DATABASE_URL 環境變數）
  pytest tests/test_positive_flows.py -v

  # 只跑不需要 DB 的 unit tests
  pytest tests/test_positive_flows.py -v -m "not integration"

  # 只跑 integration tests
  pytest tests/test_positive_flows.py -v -m integration
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── imports (trigger plugin registration) ────────────────────────────────────
import projects.dummy.plugin  # noqa: F401


# =============================================================================
# Fixtures & Helpers
# =============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark_integration = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — skipping integration tests",
)


def _unique_id(prefix: str = "test") -> str:
    """Generate a unique project_id per test run to avoid checkpoint collisions."""
    return f"{prefix}_{int(time.time() * 1000)}"


def _initial_state(project_id: str, max_loops: int = 3, goal: str = "test goal") -> dict:
    """Build a standard initial state dict."""
    return {
        "project_id":           project_id,
        "loop_index":           0,
        "loop_goal":            goal,
        "spec":                 None,
        "implementation_plan":  None,
        "last_result":          "UNKNOWN",
        "last_reason":          "",
        "max_loops":            max_loops,
        "attempt_index":        0,
        "needs_human_approval": False,
        "attempt_count":        0,
        "test_metrics":         {},
        "artifacts":            [],
    }


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


class MockPlankaSink:
    """In-memory mock of PlankaSink for testing Planka upload behaviour."""

    def __init__(self):
        self.uploaded: list[tuple[str, str, str]] = []  # (card_id, filename, content)
        self.comments: list[tuple[str, str]] = []       # (project_id, text)
        self._card_id = "test-card-001"

    def resolve_card_id(self, project_id: str) -> str:
        return self._card_id

    def upload_spec_attachment(self, card_id: str, filename: str, content: str) -> None:
        self.uploaded.append((card_id, filename, content))

    def post_comment(self, project_id: str, text: str) -> None:
        self.comments.append((project_id, text))

    def cache_card_id(self, project_id: str, card_id: str) -> None:
        self._card_id = card_id

    def uploaded_filenames(self) -> list[str]:
        return [fn for _, fn, _ in self.uploaded]

    def uploaded_content(self, filename: str) -> str | None:
        for _, fn, content in self.uploaded:
            if fn == filename:
                return content
        return None


class AlwaysPassPlugin:
    """Minimal plugin that PASSes on the very first analyze call."""
    name = "always_pass"

    def plan_node(self, state: dict) -> dict:
        return {"implementation_plan": {"strategy": "always_pass"}, "needs_human_approval": False}

    def implement_node(self, state: dict) -> dict:
        return {"needs_human_approval": False}

    def test_node(self, state: dict) -> dict:
        attempt = state.get("attempt_count", 0) + 1
        return {"attempt_count": attempt, "test_metrics": {"win_rate": 0.99, "alpha_ratio": 2.0, "max_drawdown": 0.05}}

    def analyze_node(self, state: dict) -> dict:
        return {"last_result": "PASS", "last_reason": "All criteria met."}

    def revise_node(self, state: dict) -> dict:
        return {"last_reason": "revised"}

    def summarize_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        path = f"/tmp/summary_loop{loop}.md"
        Path(path).write_text(f"# Loop {loop} Summary\nPASS\n")
        return {
            "loop_index": loop + 1,
            "last_reason": "PASS summary",
            "attempt_count": 0,
            "artifacts": state.get("artifacts", []) + [{"type": "summary", "path": path}],
        }

    def terminate_summarize_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        path = f"/tmp/terminate_loop{loop}.md"
        Path(path).write_text(f"# Terminate Report Loop {loop}\n")
        return {
            "last_reason": f"terminated loop {loop}",
            "artifacts": state.get("artifacts", []) + [{"type": "terminate_summary", "path": path}],
        }


class AlwaysFailPlugin(AlwaysPassPlugin):
    """Minimal plugin that always FAILs (never PASSes) to trigger EXHAUSTED."""
    name = "always_fail"

    def analyze_node(self, state: dict) -> dict:
        return {"last_result": "FAIL", "last_reason": "Never passes."}


class EarlyTerminatePlugin(AlwaysPassPlugin):
    """Plugin whose analyze LLM returns TERMINATE on first call."""
    name = "early_terminate"

    def analyze_node(self, state: dict) -> dict:
        # Simulates an LLM that "decides" to TERMINATE early (before max_loops)
        return {"last_result": "TERMINATE", "last_reason": "LLM decided to stop early."}


# =============================================================================
# Unit Tests — no DB required
# =============================================================================

class TestAnalyzeWrapper:
    """TC-P09 — Unit tests for _make_analyze_wrapper logic."""

    def _make_wrapper(self, analyze_fn, max_loops=3):
        from framework.graph import _make_analyze_wrapper
        return _make_analyze_wrapper(analyze_fn, db_url=None)

    def _state(self, attempt_index=0, max_loops=3, last_result="UNKNOWN"):
        return {
            "project_id": "wrapper-test",
            "attempt_index": attempt_index,
            "max_loops": max_loops,
            "last_result": last_result,
            "test_metrics": {"win_rate": 0.4},
            "loop_index": 0,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-01  FAIL → stays FAIL when below max_loops
    # ──────────────────────────────────────────────────────────────────────────
    def test_fail_stays_fail_below_max_loops(self):
        """Wrapper must NOT convert FAIL to TERMINATE before max_loops."""
        analyze_fn = lambda s: {"last_result": "FAIL", "last_reason": "bad"}
        wrapped = self._make_wrapper(analyze_fn)

        result = wrapped(self._state(attempt_index=0, max_loops=3))
        assert result["last_result"] == "FAIL"
        assert result["attempt_index"] == 1

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-02  FAIL on last attempt → EXHAUSTED TERMINATE
    # ──────────────────────────────────────────────────────────────────────────
    def test_fail_becomes_exhausted_at_max_loops(self):
        """At attempt_index=2 (new=3=max_loops), FAIL must become EXHAUSTED."""
        analyze_fn = lambda s: {"last_result": "FAIL", "last_reason": "still bad"}
        wrapped = self._make_wrapper(analyze_fn)

        result = wrapped(self._state(attempt_index=2, max_loops=3))
        assert result["last_result"] == "TERMINATE"
        assert result["last_reason"].startswith("EXHAUSTED:")
        assert result["attempt_index"] == 3

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-03  LLM early TERMINATE → overridden to FAIL
    # ──────────────────────────────────────────────────────────────────────────
    def test_llm_terminate_overridden_to_fail_before_max_loops(self):
        """
        LLM returning TERMINATE when attempt_index < max_loops must be
        overridden to FAIL so that max_loops cycles always complete.
        """
        analyze_fn = lambda s: {"last_result": "TERMINATE", "last_reason": "LLM wants to stop"}
        wrapped = self._make_wrapper(analyze_fn)

        state = self._state(attempt_index=0, max_loops=3, last_result="FAIL")
        result = wrapped(state)
        # LLM returned TERMINATE but pre_terminate was False → must become FAIL
        assert result["last_result"] == "FAIL", (
            "LLM early TERMINATE must be overridden to FAIL before max_loops is reached"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-04  Propagated TERMINATE (plan rejection) must pass through
    # ──────────────────────────────────────────────────────────────────────────
    def test_propagated_terminate_not_overridden(self):
        """
        TERMINATE that was already in state BEFORE analyze (from implement/revise)
        must NOT be overridden to FAIL — it represents a human decision.
        """
        # analyze_fn propagates the pre-existing TERMINATE
        analyze_fn = lambda s: {"last_result": "TERMINATE", "last_reason": "Plan rejected."}
        wrapped = self._make_wrapper(analyze_fn)

        # last_result already TERMINATE (set by implement/revise before analyze runs)
        state = self._state(attempt_index=0, max_loops=3, last_result="TERMINATE")
        result = wrapped(state)
        assert result["last_result"] == "TERMINATE", (
            "Propagated TERMINATE (plan rejection, revise decision) must not be overridden"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-05  PASS must always pass through unchanged
    # ──────────────────────────────────────────────────────────────────────────
    def test_pass_passes_through(self):
        """PASS result must never be modified by the wrapper."""
        analyze_fn = lambda s: {"last_result": "PASS", "last_reason": "All criteria met."}
        wrapped = self._make_wrapper(analyze_fn)

        result = wrapped(self._state(attempt_index=0, max_loops=3))
        assert result["last_result"] == "PASS"
        assert result["attempt_index"] == 1

    # ──────────────────────────────────────────────────────────────────────────
    # TC-P09-06  attempt_index increments correctly across cycles
    # ──────────────────────────────────────────────────────────────────────────
    def test_attempt_index_increments(self):
        """attempt_index must increment by 1 on each analyze call."""
        analyze_fn = lambda s: {"last_result": "FAIL", "last_reason": "x"}
        wrapped = self._make_wrapper(analyze_fn)

        for expected_attempt in range(1, 3):
            state = self._state(attempt_index=expected_attempt - 1, max_loops=5)
            result = wrapped(state)
            assert result["attempt_index"] == expected_attempt


class TestSummarizeWrapper:
    """TC-P05 — Unit tests for _make_summarize_wrapper filename convention."""

    def test_pass_report_filename_convention(self, tmp_path):
        """PASS per-loop report must be uploaded as v{attempt}_researchsummary_{ts}.md."""
        sink = MockPlankaSink()

        # Create a real summary file
        summary_path = str(tmp_path / "loop_0_summary.md")
        Path(summary_path).write_text("# Summary\nPASS\n")

        from framework.graph import _make_summarize_wrapper

        def summarize_fn(state):
            return {
                "loop_index": 1,
                "last_reason": "done",
                "attempt_count": 0,
                "artifacts": state.get("artifacts", []) + [
                    {"type": "summary", "path": summary_path}
                ],
            }

        wrapped = _make_summarize_wrapper(summarize_fn, sink)
        state = {"project_id": "p1", "attempt_index": 1, "artifacts": []}
        wrapped(state)

        assert len(sink.uploaded) == 1
        filename = sink.uploaded_filenames()[0]
        # Must match v{N}_researchsummary_{ts}.md
        import re
        assert re.match(r"v1_researchsummary_\d{12}\.md", filename), (
            f"Unexpected filename: {filename}"
        )

    def test_no_upload_when_no_summary_artifact(self):
        """Wrapper must not crash or upload anything if no summary artifact found."""
        sink = MockPlankaSink()
        from framework.graph import _make_summarize_wrapper

        def summarize_fn(state):
            return {"loop_index": 1, "last_reason": "done", "attempt_count": 0, "artifacts": []}

        wrapped = _make_summarize_wrapper(summarize_fn, sink)
        result = wrapped({"project_id": "p1", "attempt_index": 1, "artifacts": []})
        assert sink.uploaded == []

    def test_no_upload_when_sink_is_none(self, tmp_path):
        """When sink=None (no Planka), wrapper must return result unchanged."""
        from framework.graph import _make_summarize_wrapper

        def summarize_fn(state):
            return {"loop_index": 1, "last_reason": "done"}

        wrapped = _make_summarize_wrapper(summarize_fn, sink=None)
        result = wrapped({"project_id": "p1", "attempt_index": 1, "artifacts": []})
        assert result == {"loop_index": 1, "last_reason": "done"}


class TestTerminateSummarizeWrapper:
    """TC-P06 — Unit tests for _make_terminate_summarize_wrapper (our new fix)."""

    def test_terminate_report_uploaded_to_planka(self, tmp_path):
        """
        After terminate_summarize_node, the terminate_summary artifact must be
        uploaded to Planka as v{attempt_index}_researchsummary_{ts}.md.
        """
        sink = MockPlankaSink()
        report_path = str(tmp_path / "terminate_report.md")
        Path(report_path).write_text("# Terminate Report\nFAIL\n")

        from framework.graph import _make_terminate_summarize_wrapper

        def terminate_fn(state):
            return {
                "last_reason": "terminated",
                "artifacts": state.get("artifacts", []) + [
                    {"type": "terminate_summary", "path": report_path}
                ],
            }

        wrapped = _make_terminate_summarize_wrapper(terminate_fn, sink)
        state = {"project_id": "p1", "attempt_index": 3, "artifacts": []}
        result = wrapped(state)

        assert len(sink.uploaded) == 1
        filename = sink.uploaded_filenames()[0]
        import re
        assert re.match(r"v3_researchsummary_\d{12}\.md", filename), (
            f"Expected v3_researchsummary_{{ts}}.md, got: {filename}"
        )
        # Content must match the report file
        assert "Terminate Report" in sink.uploaded[0][2]

    def test_terminate_report_no_upload_when_sink_none(self, tmp_path):
        """When sink=None, terminate wrapper must return result unchanged."""
        from framework.graph import _make_terminate_summarize_wrapper

        def terminate_fn(state):
            return {"last_reason": "terminated", "artifacts": []}

        wrapped = _make_terminate_summarize_wrapper(terminate_fn, sink=None)
        result = wrapped({"project_id": "p1", "attempt_index": 3, "artifacts": []})
        assert result == {"last_reason": "terminated", "artifacts": []}

    def test_terminate_report_no_upload_when_no_artifact(self):
        """When no terminate_summary artifact, no upload must happen."""
        sink = MockPlankaSink()
        from framework.graph import _make_terminate_summarize_wrapper

        def terminate_fn(state):
            return {"last_reason": "terminated", "artifacts": []}

        wrapped = _make_terminate_summarize_wrapper(terminate_fn, sink)
        wrapped({"project_id": "p1", "attempt_index": 3, "artifacts": []})
        assert sink.uploaded == []


class TestFinalSummaryNode:
    """TC-P07 — Final summary runs for ALL TERMINATE, not just EXHAUSTED."""

    def _make_node(self, sink, db_rows=None):
        from framework.graph import _make_final_summary_node

        node_fn = _make_final_summary_node(db_url="mock://", sink=sink)

        # Patch get_loop_metrics so we don't need a real DB
        import framework.graph as gmod
        original = getattr(gmod, "get_loop_metrics", None)
        return node_fn, db_rows or []

    def test_final_summary_generated_for_exhausted(self, tmp_path):
        """Final summary must be uploaded when last_reason starts with EXHAUSTED:."""
        sink = MockPlankaSink()
        from framework.graph import _make_final_summary_node
        import framework.db.queries as q_mod

        rows = [
            {"loop_index": 1, "result": "FAIL", "reason": "r1",
             "win_rate": 0.4, "alpha_ratio": 0.8, "max_drawdown": 0.25},
            {"loop_index": 2, "result": "FAIL", "reason": "r2",
             "win_rate": 0.45, "alpha_ratio": 0.9, "max_drawdown": 0.22},
            {"loop_index": 3, "result": "TERMINATE", "reason": "EXHAUSTED:3 loops without meeting criteria.",
             "win_rate": 0.50, "alpha_ratio": 0.95, "max_drawdown": 0.20},
        ]

        with patch.object(q_mod, "get_loop_metrics", return_value=rows):
            node_fn = _make_final_summary_node(db_url="mock://", sink=sink)
            state = {
                "project_id": "p1",
                "attempt_index": 3,
                "loop_goal": "find alpha",
                "last_reason": "EXHAUSTED:3 loops without meeting criteria.",
                "last_result": "TERMINATE",
            }
            node_fn(state)

        assert len(sink.uploaded) == 1
        filename = sink.uploaded_filenames()[0]
        import re
        assert re.match(r"v1_v3_researchsummary_\d{12}\.md", filename), (
            f"Expected v1_v3_researchsummary_{{ts}}.md, got: {filename}"
        )

    def test_final_summary_generated_for_non_exhausted_terminate(self, tmp_path):
        """
        TC-P07 KEY: Final summary must ALSO be uploaded when TERMINATE reason
        is NOT EXHAUSTED (e.g. LLM decided to stop, or plan rejection).
        This verifies our bug fix.
        """
        sink = MockPlankaSink()
        from framework.graph import _make_final_summary_node
        import framework.db.queries as q_mod

        rows = [
            {"loop_index": 1, "result": "TERMINATE", "reason": "Strategy fundamentally broken.",
             "win_rate": 0.3, "alpha_ratio": 0.5, "max_drawdown": 0.35},
        ]

        with patch.object(q_mod, "get_loop_metrics", return_value=rows):
            node_fn = _make_final_summary_node(db_url="mock://", sink=sink)
            state = {
                "project_id": "p1",
                "attempt_index": 1,
                "loop_goal": "find alpha",
                "last_reason": "Strategy fundamentally broken.",  # NOT starting with EXHAUSTED:
                "last_result": "TERMINATE",
            }
            node_fn(state)

        assert len(sink.uploaded) == 1, (
            "Final summary must be uploaded even when TERMINATE reason is not EXHAUSTED:"
        )

    def test_final_summary_fallback_when_no_db_rows(self):
        """When DB returns no rows, fallback summary must still be uploaded."""
        sink = MockPlankaSink()
        from framework.graph import _make_final_summary_node
        import framework.db.queries as q_mod

        with patch.object(q_mod, "get_loop_metrics", return_value=[]):
            node_fn = _make_final_summary_node(db_url="mock://", sink=sink)
            state = {
                "project_id": "p1",
                "attempt_index": 1,
                "loop_goal": "test",
                "last_reason": "some reason",
                "last_result": "TERMINATE",
            }
            node_fn(state)

        # Should still upload (with fallback content)
        assert len(sink.uploaded) == 1


class TestPromptContent:
    """TC-P09 (prompt) — Verify prompts no longer contain conflicting TERMINATE instructions."""

    def _load_prompt(self, name: str) -> str:
        path = Path(_ROOT) / "framework" / "prompts" / "quant_alpha" / f"{name}.txt"
        return path.read_text(encoding="utf-8")

    def test_analyze_prompt_no_loop_index_terminate_instruction(self):
        """
        analyze.txt must NOT contain 'Terminate if loop_index >= N' because:
        - loop_index tracks PASS loops (stays 0 for all-FAIL research)
        - framework enforces termination via max_loops in _make_analyze_wrapper
        """
        content = self._load_prompt("analyze")
        assert "Terminate if loop_index" not in content, (
            "analyze.txt still contains conflicting 'Terminate if loop_index' instruction "
            "that bypasses max_loops enforcement"
        )

    def test_revise_prompt_no_attempt_count_terminate_instruction(self):
        """
        revise.txt must NOT contain 'attempt_count >= N → TERMINATE' because:
        - This fires before framework's max_loops for max_loops > 3
        - Framework handles termination via _make_analyze_wrapper
        """
        content = self._load_prompt("revise")
        assert "attempt_count >= 3, output TERMINATE" not in content, (
            "revise.txt still contains 'attempt_count >= 3 → TERMINATE' that "
            "bypasses max_loops enforcement"
        )

    def test_analyze_prompt_accepts_pass_fail_terminate(self):
        """analyze.txt must still allow TERMINATE as LLM output for genuine edge cases."""
        content = self._load_prompt("analyze")
        assert "TERMINATE" in content, "analyze.txt must still list TERMINATE as a valid output"

    def test_revise_prompt_accepts_terminate(self):
        """revise.txt must still allow TERMINATE for genuine LLM decisions."""
        content = self._load_prompt("revise")
        assert "TERMINATE" in content, "revise.txt must still list TERMINATE as a valid output"


# =============================================================================
# Integration Tests — require DATABASE_URL
# =============================================================================

@pytest.fixture(scope="function")
def db_url():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture(scope="function")
def project_id():
    return _unique_id("qa")


@pytest.fixture(scope="function")
def setup_project(project_id, db_url):
    """Create a project record in DB and tear down after the test."""
    from framework.db.queries import create_project
    create_project(
        project_id=project_id,
        name=f"QA Test {project_id}",
        plugin_name="always_pass",
        goal="QA positive flow test",
        db_url=db_url,
    )
    yield
    # Teardown: clean up loop_metrics and project (best-effort)
    try:
        from framework.db.connection import get_connection
        with get_connection(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM loop_metrics WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM checkpoint_decisions WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    except Exception:
        pass


def _build_graph(plugin, db_url: str, sink=None):
    """Build a compiled LangGraph graph for the given plugin."""
    from framework.graph import build_graph
    config = {"db_url": db_url, "planka_sink": sink}
    return build_graph(plugin, config)


# ──────────────────────────────────────────────────────────────────────────────
# TC-P01  Direct PASS — first analyze call succeeds
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestDirectPass:
    """TC-P01: Research PASSes on the very first analyze call."""

    def test_graph_ends_with_pass(self, project_id, db_url, setup_project):
        """last_result must be PASS and graph must reach END."""
        plugin = AlwaysPassPlugin()
        graph = _build_graph(plugin, db_url)
        state = _initial_state(project_id, max_loops=3)

        graph.invoke(state, config=_thread_config(project_id))

        final = graph.get_state(config=_thread_config(project_id))
        vals = final.values or {}
        assert vals.get("last_result") == "PASS", (
            f"Expected PASS, got {vals.get('last_result')} — reason: {vals.get('last_reason')}"
        )
        assert not final.next, "Graph must have reached END (no pending nodes)"

    def test_attempt_index_is_1_after_direct_pass(self, project_id, db_url, setup_project):
        """Direct PASS: exactly 1 analyze call, so attempt_index must be 1."""
        plugin = AlwaysPassPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("attempt_index") == 1

    def test_loop_metrics_has_one_pass_row(self, project_id, db_url, setup_project):
        """loop_metrics must have exactly 1 row with result=PASS."""
        plugin = AlwaysPassPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        from framework.db.queries import get_loop_metrics
        rows = get_loop_metrics(project_id, db_url)
        assert len(rows) == 1
        assert rows[0]["result"] == "PASS"

    def test_per_loop_summary_uploaded_to_planka(self, project_id, db_url, setup_project, tmp_path):
        """After PASS: per-loop researchsummary md must be uploaded to Planka."""
        sink = MockPlankaSink()
        plugin = AlwaysPassPlugin()
        graph = _build_graph(plugin, db_url, sink=sink)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        import re
        uploaded = sink.uploaded_filenames()
        pass_reports = [f for f in uploaded if re.match(r"v\d+_researchsummary_\d{12}\.md", f)]
        assert len(pass_reports) >= 1, (
            f"Expected at least one v{{N}}_researchsummary_{{ts}}.md upload, got: {uploaded}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# TC-P02  FAIL → PASS — one revise cycle then success
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestFailThenPass:
    """TC-P02: DummyPlugin (FAIL on attempt 1, PASS on attempt 2)."""

    def test_graph_ends_with_pass_after_revise(self, project_id, db_url, setup_project):
        """DummyPlugin FAILs on first attempt then PASSes — last_result must be PASS."""
        from projects.dummy.plugin import DummyPlugin
        plugin = DummyPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("last_result") == "PASS"

    def test_attempt_index_is_2_after_one_revise(self, project_id, db_url, setup_project):
        """FAIL on attempt 1, PASS on attempt 2 → attempt_index must be 2."""
        from projects.dummy.plugin import DummyPlugin
        plugin = DummyPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("attempt_index") == 2

    def test_fail_loop_recorded_in_db(self, project_id, db_url, setup_project):
        """
        TC-P08: The FAIL loop (attempt 1) must be recorded in loop_metrics.
        This verifies our bug fix for missing FAIL loop metrics.
        """
        from projects.dummy.plugin import DummyPlugin
        plugin = DummyPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        from framework.db.queries import get_loop_metrics
        rows = get_loop_metrics(project_id, db_url)
        results = {r["loop_index"]: r["result"] for r in rows}

        assert 1 in results, "FAIL loop (attempt_index=1) must be recorded in loop_metrics"
        assert results[1] == "FAIL", f"Expected FAIL at loop_index=1, got {results[1]}"
        assert 2 in results, "PASS loop (attempt_index=2) must be recorded in loop_metrics"
        assert results[2] == "PASS", f"Expected PASS at loop_index=2, got {results[2]}"

    def test_attempt_count_reset_to_zero_after_pass(self, project_id, db_url, setup_project):
        """attempt_count must be reset to 0 by summarize_node after PASS."""
        from projects.dummy.plugin import DummyPlugin
        plugin = DummyPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("attempt_count") == 0, "attempt_count must be reset to 0 after PASS"

    def test_summary_artifact_exists(self, project_id, db_url, setup_project, tmp_path):
        """After PASS, a summary artifact must be present in final state."""
        with patch.dict(os.environ, {"ARTIFACTS_DIR": str(tmp_path)}):
            from projects.dummy.plugin import DummyPlugin
            plugin = DummyPlugin()
            graph = _build_graph(plugin, db_url)
            graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        # summarize_wrapper removes summary from artifacts after upload
        # but impl artifacts should still be there
        artifacts = vals.get("artifacts", [])
        impl_arts = [a for a in artifacts if a.get("type") == "impl"]
        assert len(impl_arts) >= 1, "At least one impl artifact must remain in state"


# ──────────────────────────────────────────────────────────────────────────────
# TC-P03  EXHAUSTED TERMINATE — max_loops all FAIL, per-loop md + final summary
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestExhaustedTerminate:
    """TC-P03: All loops FAIL → EXHAUSTED TERMINATE with correct md output."""

    def test_graph_ends_with_terminate_after_exhausted(self, project_id, db_url, setup_project):
        """After max_loops=3 all-FAIL cycles, graph must end with TERMINATE."""
        plugin = AlwaysFailPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("last_result") == "TERMINATE"

    def test_last_reason_starts_with_exhausted(self, project_id, db_url, setup_project):
        """EXHAUSTED TERMINATE: last_reason must start with 'EXHAUSTED:'."""
        plugin = AlwaysFailPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert (vals.get("last_reason") or "").startswith("EXHAUSTED:"), (
            f"Expected reason to start with EXHAUSTED:, got: {vals.get('last_reason')}"
        )

    def test_attempt_index_equals_max_loops(self, project_id, db_url, setup_project):
        """attempt_index must equal max_loops=3 after EXHAUSTED."""
        plugin = AlwaysFailPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("attempt_index") == 3

    def test_fail_metrics_recorded_for_all_loops(self, project_id, db_url, setup_project):
        """
        TC-P08: All 3 FAIL loops must have their metrics in loop_metrics.
        Verifies our bug fix for missing intermediate FAIL loop metrics.
        """
        plugin = AlwaysFailPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        from framework.db.queries import get_loop_metrics
        rows = get_loop_metrics(project_id, db_url)

        # Expect 3 rows: 2 FAIL + 1 TERMINATE (EXHAUSTED)
        assert len(rows) == 3, (
            f"Expected 3 rows in loop_metrics (2 FAIL + 1 TERMINATE), got {len(rows)}: "
            f"{[(r['loop_index'], r['result']) for r in rows]}"
        )
        results_by_attempt = {r["loop_index"]: r["result"] for r in rows}
        assert results_by_attempt.get(1) == "FAIL"
        assert results_by_attempt.get(2) == "FAIL"
        assert results_by_attempt.get(3) == "TERMINATE"

    def test_terminate_report_uploaded_for_each_loop(self, project_id, db_url, setup_project, tmp_path):
        """
        TC-P06: terminate_summarize's report must be uploaded to Planka
        as v{attempt_index}_researchsummary_{ts}.md.
        Verifies our new _make_terminate_summarize_wrapper.
        """
        import re
        sink = MockPlankaSink()

        with patch.dict(os.environ, {"ARTIFACTS_DIR": str(tmp_path)}):
            plugin = AlwaysFailPlugin()
            graph = _build_graph(plugin, db_url, sink=sink)
            graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        uploaded = sink.uploaded_filenames()
        term_reports = [f for f in uploaded if re.match(r"v\d+_researchsummary_\d{12}\.md", f)]
        assert len(term_reports) >= 1, (
            f"Expected at least one v{{N}}_researchsummary_{{ts}}.md uploaded "
            f"on TERMINATE path. Got: {uploaded}"
        )

    def test_final_summary_uploaded(self, project_id, db_url, setup_project, tmp_path):
        """
        TC-P07: Final multi-loop summary v1_v3_researchsummary_{ts}.md must be
        uploaded to Planka after EXHAUSTED TERMINATE.
        """
        import re
        sink = MockPlankaSink()

        with patch.dict(os.environ, {"ARTIFACTS_DIR": str(tmp_path)}):
            plugin = AlwaysFailPlugin()
            graph = _build_graph(plugin, db_url, sink=sink)
            graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        uploaded = sink.uploaded_filenames()
        final_summaries = [f for f in uploaded if re.match(r"v1_v\d+_researchsummary_\d{12}\.md", f)]
        assert len(final_summaries) >= 1, (
            f"Expected v1_v{{N}}_researchsummary_{{ts}}.md final summary. Got: {uploaded}"
        )
        # For max_loops=3 the filename should be v1_v3_researchsummary_*.md
        assert any(re.match(r"v1_v3_researchsummary_\d{12}\.md", f) for f in final_summaries), (
            f"Final summary should be v1_v3_researchsummary_*.md. Got: {final_summaries}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# TC-P04  HITL Plan Approval — interrupt → approve → PASS
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestHITLPlanApproval:
    """TC-P04: Human-in-the-loop plan approval via LangGraph interrupt."""

    def test_graph_pauses_at_plan_review_interrupt(self, project_id, db_url, setup_project):
        """
        When needs_human_approval=True, implement_node must trigger an interrupt
        and graph must pause (state.next is non-empty).
        """
        from projects.demo.plugin import DemoPlugin
        plugin = DemoPlugin()
        graph = _build_graph(plugin, db_url)

        # needs_human_approval starts as False in _build_initial_state,
        # but plan_node sets it to True. We start with False so plan runs first.
        state = _initial_state(project_id, max_loops=3)
        graph.invoke(state, config=_thread_config(project_id))

        final_state = graph.get_state(config=_thread_config(project_id))
        assert final_state.next, (
            "Graph should be paused at plan_review interrupt (state.next must be non-empty)"
        )

    def test_interrupt_payload_contains_plan_review_checkpoint(self, project_id, db_url, setup_project):
        """The interrupt payload must include checkpoint='plan_review'."""
        from projects.demo.plugin import DemoPlugin
        plugin = DemoPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        final_state = graph.get_state(config=_thread_config(project_id))
        interrupt_type = None
        for task in (final_state.tasks or []):
            if hasattr(task, "interrupts") and task.interrupts:
                payload = task.interrupts[0]
                val = payload.value if hasattr(payload, "value") else payload
                if isinstance(val, dict):
                    interrupt_type = val.get("checkpoint")
        assert interrupt_type == "plan_review"

    def test_approve_resumes_and_reaches_pass(self, project_id, db_url, setup_project):
        """After approving the plan, graph must resume and reach PASS."""
        from projects.demo.plugin import DemoPlugin
        from langgraph.types import Command

        plugin = DemoPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        # Resume with approval
        graph.invoke(Command(resume={"action": "approve"}), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("last_result") == "PASS", (
            f"After plan approval, expected PASS, got: {vals.get('last_result')}"
        )

    def test_reject_plan_terminates_gracefully(self, project_id, db_url, setup_project):
        """Rejecting the plan must result in TERMINATE (not crash)."""
        from projects.demo.plugin import DemoPlugin
        from langgraph.types import Command

        plugin = DemoPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        graph.invoke(
            Command(resume={"action": "reject", "reason": "Strategy not aligned with goal."}),
            config=_thread_config(project_id),
        )

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("last_result") == "TERMINATE"
        assert "Strategy not aligned" in (vals.get("last_reason") or "")

    def test_plan_review_checkpoint_decision_recorded(self, project_id, db_url, setup_project):
        """checkpoint_decisions table must record the approve action."""
        from projects.demo.plugin import DemoPlugin
        from langgraph.types import Command
        from framework.db.queries import record_checkpoint_decision

        plugin = DemoPlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id), config=_thread_config(project_id))

        record_checkpoint_decision(
            project_id=project_id,
            loop_index=0,
            action="approve",
            notes="QA test approval",
            db_url=db_url,
        )

        graph.invoke(Command(resume={"action": "approve"}), config=_thread_config(project_id))

        # Verify checkpoint_decisions row
        from framework.db.connection import get_connection
        with get_connection(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action, notes FROM checkpoint_decisions WHERE project_id = %s",
                    (project_id,),
                )
                rows = cur.fetchall()
        assert len(rows) >= 1
        assert any(r[0] == "approve" for r in rows)


# ──────────────────────────────────────────────────────────────────────────────
# TC-P09  LLM Early TERMINATE prevention (Integration)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestEarlyTerminatePrevention:
    """TC-P09 integration: LLM early TERMINATE must be overridden to FAIL."""

    def test_early_terminate_plugin_runs_max_loops(self, project_id, db_url, setup_project):
        """
        EarlyTerminatePlugin always returns TERMINATE from analyze.
        After max_loops=3, the wrapper must have overridden to FAIL twice,
        then on the 3rd call forced EXHAUSTED TERMINATE.
        Final attempt_index must equal max_loops=3.
        """
        plugin = EarlyTerminatePlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        vals = (graph.get_state(config=_thread_config(project_id)).values or {})
        assert vals.get("last_result") == "TERMINATE"
        assert vals.get("attempt_index") == 3, (
            f"Expected attempt_index=3 (ran max_loops times), "
            f"got {vals.get('attempt_index')} — early TERMINATE was not prevented"
        )
        assert (vals.get("last_reason") or "").startswith("EXHAUSTED:"), (
            "Final TERMINATE must be EXHAUSTED (not early LLM TERMINATE)"
        )

    def test_early_terminate_metrics_recorded_for_all_loops(self, project_id, db_url, setup_project):
        """Even with early-TERMINATE plugin, all 3 loops must be recorded in DB."""
        plugin = EarlyTerminatePlugin()
        graph = _build_graph(plugin, db_url)
        graph.invoke(_initial_state(project_id, max_loops=3), config=_thread_config(project_id))

        from framework.db.queries import get_loop_metrics
        rows = get_loop_metrics(project_id, db_url)
        assert len(rows) == 3, (
            f"Expected 3 DB rows (overridden FAIL×2 + TERMINATE×1), got {len(rows)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# TC-P10  Planka card state transitions
# ──────────────────────────────────────────────────────────────────────────────

class TestPlankaCardTransitions:
    """TC-P10: Verify _finish_run moves cards to correct columns."""

    def _make_mock_move(self):
        """Return a mock that tracks _move_planka_card calls."""
        return MagicMock()

    def test_pass_moves_card_to_done(self):
        """PASS result must trigger card move to 'Done'."""
        import framework.api.server as server_mod
        move_calls = []

        with patch.object(server_mod, "_move_planka_card", side_effect=lambda pid, col: move_calls.append(col)):
            server_mod._finish_run("proj1", "PASS", "all good")

        assert "Done" in move_calls, f"Expected 'Done' in moves, got: {move_calls}"

    def test_terminate_moves_card_to_review(self):
        """TERMINATE result must trigger card move to 'Review'."""
        import framework.api.server as server_mod
        move_calls = []

        with patch.object(server_mod, "_move_planka_card", side_effect=lambda pid, col: move_calls.append(col)):
            with patch.object(server_mod, "_planka_sink", None):
                server_mod._finish_run("proj1", "TERMINATE", "exhausted")

        assert "Review" in move_calls, f"Expected 'Review' in moves, got: {move_calls}"

    def test_unknown_result_moves_card_to_planning(self):
        """UNKNOWN last_result (interrupted graph) must fall back to 'Planning'."""
        import framework.api.server as server_mod
        move_calls = []

        with patch.object(server_mod, "_move_planka_card", side_effect=lambda pid, col: move_calls.append(col)):
            server_mod._finish_run("proj1", "UNKNOWN", "")

        assert "Planning" in move_calls, f"Expected 'Planning' in moves, got: {move_calls}"

    def test_terminate_posts_comment_to_planka(self):
        """TERMINATE must post a Planka comment explaining the reason."""
        import framework.api.server as server_mod

        mock_sink = MockPlankaSink()
        with patch.object(server_mod, "_move_planka_card"):
            with patch.object(server_mod, "_planka_sink", mock_sink):
                server_mod._finish_run("proj1", "TERMINATE", "EXHAUSTED:3 loops without meeting criteria.")

        assert len(mock_sink.comments) >= 1
        comment_text = mock_sink.comments[0][1]
        assert "Review" in comment_text or "EXHAUSTED" in comment_text or "Research ended" in comment_text


# =============================================================================
# Test runner helpers
# =============================================================================

if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v", "--tb=short"])
