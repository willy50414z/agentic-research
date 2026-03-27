"""
framework/graph.py

Core LangGraph graph builder for the agentic research workflow.

Graph flow:
    START → plan → implement → test → analyze
                                          │ FAIL → revise → implement
                                          │ PASS → summarize → record_metrics → END
                                          │ TERMINATE → record_terminate_metrics
                                          │               → terminate_summarize
                                          │               → final_summary → END

End conditions (handled by server.py after graph completes):
    last_result == "PASS"      → card moves to Done
    last_result == "TERMINATE" → card moves to Review (reason posted as Planka comment)
                                  v{attempt_index}_researchsummary_{ts}.md uploaded per loop
                                  v1_v{n}_researchsummary_{ts}.md final summary uploaded

max_loops enforcement:
    Framework analyze wrapper increments attempt_index after each analyze call.
    If attempt_index >= max_loops and result != PASS → override to TERMINATE with
    last_reason = "EXHAUSTED:<N> loops without meeting criteria."
    FAIL loop metrics are recorded to DB after each FAIL analyze call.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional

import psycopg
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.postgres import PostgresSaver

from .plugin_interface import ResearchPlugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class ResearchState(TypedDict):
    project_id: str
    loop_index: int
    loop_goal: str
    spec: Optional[dict]                # full structured spec injected at start time
    implementation_plan: Optional[dict]
    last_result: str                    # PASS | TERMINATE | UNKNOWN
    last_reason: str
    max_loops: int                      # from Planka card custom field (default 3)
    attempt_index: int                  # total analyze calls (incremented by framework wrapper)
    needs_human_approval: bool
    attempt_count: int                  # tracks revise→implement attempts within one loop
    test_metrics: dict                  # latest test result metrics (plugin-defined keys)
    artifacts: list                     # lightweight refs (local paths or MinIO keys)


# ---------------------------------------------------------------------------
# Framework wrappers
# ---------------------------------------------------------------------------

def _make_analyze_wrapper(analyze_fn, db_url: str = None):
    """
    Framework wrapper around plugin.analyze_node.
    Increments attempt_index and enforces max_loops.
    - Records FAIL loop metrics to DB (per loop, not just terminal TERMINATE).
    - If attempt_index >= max_loops and result != PASS:
        overrides last_result → "TERMINATE"
        last_reason → "EXHAUSTED:<N> loops without meeting criteria."
    """
    def wrapped(state: dict) -> dict:
        # Track if TERMINATE was already set before analyze runs (plan rejection / revise decision)
        pre_terminate = state.get("last_result") == "TERMINATE"
        result = analyze_fn(state)
        new_attempt = state.get("attempt_index", 0) + 1
        max_loops = state.get("max_loops", 3)
        updates = {**result, "attempt_index": new_attempt}
        if updates.get("last_result") != "PASS" and new_attempt >= max_loops:
            updates["last_result"] = "TERMINATE"
            updates["last_reason"] = f"EXHAUSTED:{max_loops} loops without meeting criteria."
        elif (updates.get("last_result") == "TERMINATE" and not pre_terminate
              and new_attempt < max_loops):
            # LLM chose TERMINATE early — override to FAIL so max_loops cycles are completed
            updates["last_result"] = "FAIL"
        # Record metrics for FAIL loops so get_loop_metrics has complete history
        if updates.get("last_result") == "FAIL" and db_url:
            try:
                from .db.queries import record_loop_metrics
                record_loop_metrics(
                    project_id=state.get("project_id", ""),
                    loop_index=new_attempt,
                    result="FAIL",
                    reason=updates.get("last_reason", ""),
                    report_path=None,
                    metrics=state.get("test_metrics", {}),
                    db_url=db_url,
                )
            except Exception as e:
                logger.warning("FAIL loop_metrics write failed (non-blocking): %s", e)
        return updates
    return wrapped


def _make_summarize_wrapper(summarize_fn, sink):
    """
    Framework wrapper around plugin.summarize_node.
    After each PASS loop: uploads the summary md to the Planka card as
    v{attempt_index}_researchsummary_{YYYYMMDDHHMM}.md and removes it from
    artifacts (not tracked locally — lives in Planka).
    """
    def wrapped(state: dict) -> dict:
        result = summarize_fn(state)
        if sink is None:
            return result

        artifacts = (
            result.get("artifacts")
            if result.get("artifacts") is not None
            else state.get("artifacts", [])
        )
        summary_art = next(
            (a for a in reversed(artifacts) if a.get("type") == "summary"), None
        )
        if summary_art:
            path = summary_art.get("path", "")
            attempt = state.get("attempt_index", 0)  # already incremented by analyze wrapper
            ts = datetime.now().strftime("%Y%m%d%H%M")
            filename = f"v{attempt}_researchsummary_{ts}.md"
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                project_id = state.get("project_id", "")
                card_id = sink.resolve_card_id(project_id)
                if card_id:
                    sink.upload_spec_attachment(card_id, filename, content)
                    logger.info(
                        "Uploaded per-loop report '%s' to Planka card '%s'.",
                        filename, card_id,
                    )
                Path(path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Per-loop Planka upload failed: %s", e)
            # Remove from artifacts: the file lives in Planka, not tracked locally
            cleaned = [a for a in artifacts if a is not summary_art]
            result = {**result, "artifacts": cleaned}
        return result
    return wrapped


def _make_terminate_summarize_wrapper(terminate_summarize_fn, sink):
    """
    Framework wrapper around plugin.terminate_summarize_node.
    After TERMINATE: uploads the terminate report md to the Planka card as
    v{attempt_index}_researchsummary_{YYYYMMDDHHMM}.md.
    """
    def wrapped(state: dict) -> dict:
        result = terminate_summarize_fn(state)
        if sink is None:
            return result

        artifacts = (
            result.get("artifacts")
            if result.get("artifacts") is not None
            else state.get("artifacts", [])
        )
        term_art = next(
            (a for a in reversed(artifacts) if a.get("type") == "terminate_summary"), None
        )
        if term_art:
            path = term_art.get("path", "")
            attempt = state.get("attempt_index", 0)
            ts = datetime.now().strftime("%Y%m%d%H%M")
            filename = f"v{attempt}_researchsummary_{ts}.md"
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                project_id = state.get("project_id", "")
                card_id = sink.resolve_card_id(project_id)
                if card_id:
                    sink.upload_spec_attachment(card_id, filename, content)
                    logger.info(
                        "Uploaded terminate report '%s' to Planka card '%s'.",
                        filename, card_id,
                    )
                Path(path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Terminate report Planka upload failed: %s", e)
        return result
    return wrapped


def _make_final_summary_node(db_url: str, sink):
    """
    Framework node: generates a Chinese multi-loop summary report on TERMINATE.
    Runs for ALL TERMINATE cases (not just EXHAUSTED).
    Uploads v1_vN_researchsummary_{YYYYMMDDHHMM}.md to the Planka card.
    """
    def node(state: dict) -> dict:
        project_id = state.get("project_id", "")
        n = state.get("attempt_index", 0)
        try:
            from framework.db.queries import get_loop_metrics
            rows = get_loop_metrics(project_id, db_url)

            llm_fn = _try_build_llm_fn()
            if llm_fn:
                prompt = _build_final_summary_prompt(rows, n, state.get("loop_goal", ""))
                try:
                    report_md = llm_fn(prompt)
                except Exception as e:
                    logger.warning("LLM final summary call failed: %s — using fallback", e)
                    report_md = _fallback_summary(rows, n, state.get("loop_goal", ""))
            else:
                report_md = _fallback_summary(rows, n, state.get("loop_goal", ""))

            ts = datetime.now().strftime("%Y%m%d%H%M")
            filename = f"v1_v{n}_researchsummary_{ts}.md"
            card_id = sink.resolve_card_id(project_id) if sink else None
            if card_id and sink:
                sink.upload_spec_attachment(card_id, filename, report_md)
                logger.info(
                    "Uploaded final summary '%s' to Planka card '%s'.", filename, card_id
                )
        except Exception as e:
            logger.warning("Final summary generation failed: %s", e)
        return {}
    return node


def _try_build_llm_fn():
    """Try to build an LLM callable from the LLM_CHAIN env var."""
    try:
        from framework.llm_providers import LLMProviderFactory
        for provider in (os.getenv("LLM_CHAIN", "") or "").split(","):
            provider = provider.strip()
            if not provider:
                continue
            fn = LLMProviderFactory.build(provider)
            if fn is not None:
                return fn
    except Exception:
        pass
    return None


def _build_final_summary_prompt(rows: list[dict], n: int, goal: str) -> str:
    rows_text = ""
    for r in rows:
        i = r.get("loop_index", 1)  # loop_index is 1-based (attempt_index)
        result = r.get("result", "UNKNOWN")
        reason = r.get("reason", "")
        wr = r.get("win_rate")
        ar = r.get("alpha_ratio")
        dd = r.get("max_drawdown")
        metrics_str = (
            f"win_rate={wr:.4f}, alpha_ratio={ar:.4f}, max_drawdown={dd:.4f}"
            if wr is not None else "（無量化指標資料）"
        )
        rows_text += (
            f"\n第 {i} 輪：result={result}, {metrics_str}\n  原因：{reason}\n"
        )

    return f"""你是量化策略研究助理，請根據以下多輪研究資料，用繁體中文產出一份完整的研究總結報告。

研究目標：{goal}
總執行輪數：{n}

各輪資料：
{rows_text}

請按以下格式輸出 Markdown 報告（直接輸出報告內容，不要加任何前綴說明）：

# 研究總結報告

## 概述
（整體 {n} 輪研究的結論摘要，說明是否達到研究目標）

## 各輪詳情

（對每一輪按以下格式輸出）

### 第 N 輪
- **測試目標**：本輪的具體策略目標或假設
- **測試結果**：量化指標數據（win_rate、alpha_ratio、max_drawdown 等）
- **結果判定**：PASS / FAIL / TERMINATE
- **改善方向**：下一步建議的改進方向

## 整體結論與建議下一步
（綜合所有輪次的發現，給出明確的後續策略建議）
"""


def _fallback_summary(rows: list[dict], n: int, goal: str) -> str:
    """Fallback plain-text summary when LLM is unavailable."""
    lines = [
        f"# 研究總結報告\n",
        f"**研究目標**：{goal}",
        f"**總執行輪數**：{n}\n",
        "## 各輪詳情\n",
    ]
    for r in rows:
        i = r.get("loop_index", 1)  # loop_index is 1-based (attempt_index)
        result = r.get("result", "UNKNOWN")
        reason = r.get("reason", "")
        wr = r.get("win_rate")
        ar = r.get("alpha_ratio")
        dd = r.get("max_drawdown")
        metrics_line = (
            f"win_rate={wr:.4f}, alpha_ratio={ar:.4f}, max_drawdown={dd:.4f}"
            if wr is not None else "無量化指標"
        )
        lines += [
            f"### 第 {i} 輪",
            f"- **結果判定**：{result}",
            f"- **測試結果**：{metrics_line}",
            f"- **原因**：{reason}\n",
        ]
    lines += [
        "## 建議下一步\n",
        "請檢視各輪結果後調整策略參數，移回 Spec Pending Review 重新啟動。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _analyze_router(state: ResearchState) -> str:
    result = state.get("last_result", "UNKNOWN")
    if result == "PASS":
        return "pass"
    if result == "TERMINATE":
        return "terminate"
    return "fail"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _make_record_metrics_node(db_url: str):
    """
    Framework node: writes loop_metrics after every PASS.
    Uses attempt_index (1-based) as the loop_index key for consistency with FAIL/TERMINATE rows.
    """
    from .db.queries import record_loop_metrics

    def record_metrics_node(state: dict) -> dict:
        attempt = state.get("attempt_index", 0)  # already incremented by analyze wrapper
        try:
            record_loop_metrics(
                project_id=state.get("project_id", ""),
                loop_index=attempt,
                result=state.get("last_result", "PASS"),
                reason=state.get("last_reason", ""),
                report_path=next(
                    (a["path"] for a in reversed(state.get("artifacts", []))
                     if a.get("type") == "summary"),
                    None,
                ),
                metrics=state.get("test_metrics", {}),
                db_url=db_url,
            )
        except Exception as e:
            logger.warning("loop_metrics write failed (non-blocking): %s", e)
        return {}

    return record_metrics_node


def _make_record_terminate_metrics_node(db_url: str):
    """
    Framework node: writes loop_metrics when a loop terminates without PASS.
    Runs on the TERMINATE path from analyze (before terminate_summarize).
    """
    from .db.queries import record_loop_metrics

    def record_terminate_metrics_node(state: dict) -> dict:
        attempt = state.get("attempt_index", 0)  # already incremented by analyze wrapper
        try:
            record_loop_metrics(
                project_id=state.get("project_id", ""),
                loop_index=attempt,
                result=state.get("last_result", "TERMINATE"),
                reason=state.get("last_reason", ""),
                report_path=None,
                metrics=state.get("test_metrics", {}),
                db_url=db_url,
            )
            logger.info(
                "terminate_metrics recorded: project=%s attempt=%d",
                state.get("project_id"), attempt,
            )
        except Exception as e:
            logger.warning("terminate_metrics write failed (non-blocking): %s", e)
        return {}

    return record_terminate_metrics_node


def _make_node_logger(node_name: str, node_fn, sink):
    """
    Wrap a plugin node function to post a Planka comment after each execution.
    If sink is None, returns node_fn unchanged (no-op).
    """
    if sink is None:
        return node_fn

    def wrapped(state: dict) -> dict:
        result = node_fn(state)
        _post_node_comment(node_name, state, result or {}, sink)
        return result

    wrapped.__name__ = node_fn.__name__ if hasattr(node_fn, "__name__") else node_name
    return wrapped


def _post_node_comment(node_name: str, state: dict, result: dict, sink) -> None:
    """Format a structured Planka comment for a completed node execution."""
    project_id = state.get("project_id", "unknown")
    merged = {**state, **result}
    loop = merged.get("loop_index", 0)
    lines = [f"[{node_name.upper()}] Loop {loop}"]

    if node_name == "plan":
        plan = merged.get("implementation_plan") or {}
        lines.append(f"strategy: {plan.get('strategy_type', '?')}")
        lines.append(f"goal: {str(merged.get('loop_goal', ''))[:200]}")
    elif node_name == "implement":
        lines.append(f"attempt #{merged.get('attempt_count', '?')}")
        lines.append(f"artifacts: {len(merged.get('artifacts', []))} total")
    elif node_name == "test":
        m = merged.get("test_metrics") or {}
        if m:
            lines.append(
                f"win_rate={m.get('win_rate', 0):.4f}  "
                f"alpha={m.get('alpha_ratio', 0):.4f}  "
                f"drawdown={m.get('max_drawdown', 0):.4f}  "
                f"trades={m.get('n_trades', 0)}"
            )
    elif node_name == "analyze":
        lines.append(f"result: {merged.get('last_result', '?')} (attempt #{merged.get('attempt_index', '?')})")
        lines.append(str(merged.get("last_reason", ""))[:300])
    elif node_name in ("revise", "summarize", "terminate_summarize"):
        lines.append(str(merged.get("last_reason", ""))[:400])
    elif node_name in ("record_metrics", "record_terminate_metrics"):
        lines.append("loop metrics saved to DB")

    try:
        sink.post_comment(project_id, "\n".join(lines))
    except Exception as e:
        logger.warning("Planka comment failed for node '%s': %s", node_name, e)


def build_graph(plugin: ResearchPlugin, config: dict):
    """
    Compile a LangGraph graph wired with the given plugin's node implementations.

    Args:
        plugin:  An instantiated ResearchPlugin.
        config:  Dict with keys:
                   db_url (str)      — psycopg3 connection string
                   planka_sink       — PlankaSink instance (optional)

    Returns:
        Compiled LangGraph graph with PostgresSaver checkpointer.
    """
    db_url = config.get("db_url") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("db_url must be provided in config or DATABASE_URL env var.")

    sink = config.get("planka_sink")

    workflow = StateGraph(ResearchState)

    # --- Nodes ---
    W = lambda name, fn: _make_node_logger(name, fn, sink)  # noqa: E731
    workflow.add_node("plan",                     W("plan",                     plugin.plan_node))
    workflow.add_node("implement",                W("implement",                plugin.implement_node))
    workflow.add_node("test",                     W("test",                     plugin.test_node))
    workflow.add_node("analyze",                  W("analyze",                  _make_analyze_wrapper(plugin.analyze_node, db_url)))
    workflow.add_node("revise",                   W("revise",                   plugin.revise_node))
    workflow.add_node("summarize",                W("summarize",                _make_summarize_wrapper(plugin.summarize_node, sink)))
    workflow.add_node("terminate_summarize",      W("terminate_summarize",      _make_terminate_summarize_wrapper(plugin.terminate_summarize_node, sink)))
    workflow.add_node("record_metrics",           W("record_metrics",           _make_record_metrics_node(db_url)))
    workflow.add_node("record_terminate_metrics", W("record_terminate_metrics", _make_record_terminate_metrics_node(db_url)))
    workflow.add_node("final_summary",            _make_final_summary_node(db_url, sink))

    # --- Edges ---
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "implement")
    workflow.add_edge("implement", "test")
    workflow.add_edge("test", "analyze")

    workflow.add_conditional_edges(
        "analyze",
        _analyze_router,
        {"fail": "revise", "pass": "summarize", "terminate": "record_terminate_metrics"},
    )

    # PASS path: summarize → record_metrics → END (first PASS ends immediately → Done)
    workflow.add_edge("revise", "implement")
    workflow.add_edge("summarize", "record_metrics")
    workflow.add_edge("record_metrics", END)

    # TERMINATE path: record → summarize → final_summary (Chinese report if EXHAUSTED) → END
    workflow.add_edge("record_terminate_metrics", "terminate_summarize")
    workflow.add_edge("terminate_summarize", "final_summary")
    workflow.add_edge("final_summary", END)

    # --- Checkpointer ---
    conn = psycopg.connect(db_url, autocommit=True)
    checkpointer = PostgresSaver(conn)
    try:
        checkpointer.setup()
    except Exception as e:
        # setup() is not idempotent — UniqueViolation means tables already exist, safe to ignore
        if "already exists" in str(e).lower() or "unique" in str(e).lower():
            logger.debug("checkpointer.setup() skipped (tables already exist): %s", e)
        else:
            raise

    compiled = workflow.compile(checkpointer=checkpointer)
    logger.info("Graph compiled for plugin '%s'.", plugin.name)
    return compiled


# ---------------------------------------------------------------------------
# Graph cache (one graph instance per plugin name, shared across requests)
# ---------------------------------------------------------------------------

_graph_cache: dict[str, object] = {}


def get_or_build_graph(plugin: ResearchPlugin, config: dict):
    """Return a cached compiled graph for the given plugin name."""
    key = plugin.name
    if key not in _graph_cache:
        _graph_cache[key] = build_graph(plugin, config)
    return _graph_cache[key]
