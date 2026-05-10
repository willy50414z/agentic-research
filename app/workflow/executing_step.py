"""
app/workflow/executing_step.py

Step-based workflow dispatcher — replaces LangGraph StateGraph.

Each research project advances through atomic steps tracked by DB workflow_step:
    plan → implement → test → analyze → summarize → done
                                  ↑FAIL↓ (retry until max_loops)

Usage:
    from app.workflow.executing_step import dispatch_step

    dispatch_step(project_id, db_url=DATABASE_URL, sink=planka_sink, move_card_fn=move_card)
"""

import io
import json
import logging
import os
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.db.queries import (
    get_project, set_workflow_step,
    record_loop_metrics, merge_config,
)
from app.freqtrade.steps import (
    plan_step, implement_step, test_step, analyze_step, summarize_step,
    revise_step, terminate_summarize_step,
)
from app.workflow.constants import (
    AnalysisResult, MoveCardFn, PlankaColumn, WorkflowSink, WorkflowStep,
)
from app.workflow.error_report import write_error_report

logger = logging.getLogger(__name__)

_PLAN_PREVIEW_MAX_CHARS = 1000

# "project_id": immutable primary key, never patched via config merge.
# "paused_at":  managed exclusively via explicit merge_config() calls (_run_plan sets it,
#               _run_implement clears it); must not be silently overwritten by step return values.
_MERGE_EXCLUDED: frozenset[str] = frozenset({"project_id", "paused_at"})

_ANALYZE_NEXT_STEP: dict[str, WorkflowStep] = {
    AnalysisResult.PASS:      WorkflowStep.SUMMARIZE,
    AnalysisResult.FAIL:      WorkflowStep.REVISE,
    AnalysisResult.TERMINATE: WorkflowStep.TERMINATE,
}

_StepHandler = Callable[[str, dict, str | None, WorkflowSink | None, MoveCardFn], None]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def dispatch_step(
    project_id: str,
    db_url: str | None = None,
    sink: WorkflowSink | None = None,
    move_card_fn: MoveCardFn = None,
) -> None:
    """
    Reads the workflow step from DB and runs the corresponding step function.
    Loops through steps automatically until a terminal state (DONE/TERMINATE),
    a HITL pause, or an exception — no intermediate Planka webhook needed.
    """
    while True:
        project = get_project(project_id, db_url)
        if project is None:
            logger.error("[dispatch] project '%s' not found in DB.", project_id)
            return

        cfg = project.get("config") or {}
        if cfg.get("review_in_progress"):
            logger.warning(
                "[dispatch] SKIP — spec review still in progress for project '%s'. "
                "Wait for review to complete or clear review_in_progress via scan_stalled_reviews.",
                project_id,
            )
            return

        raw_step = project.get("workflow_step")
        try:
            step = WorkflowStep(raw_step) if raw_step else WorkflowStep.PLAN
        except ValueError:
            logger.warning("[dispatch] unrecognised workflow_step '%s' for project '%s'.", raw_step, project_id)
            return

        logger.info("[workflow][dispatch] project='%s' step='%s'", project_id, step)

        state = _build_state(project)

        handler: _StepHandler | None = _STEP_HANDLERS.get(step)
        if handler is None:
            logger.info("[workflow][dispatch] terminal step '%s' for project '%s' — workflow complete.", step, project_id)
            return

        try:
            handler(project_id, state, db_url=db_url, sink=sink, move_card_fn=move_card_fn)
        except Exception as e:
            logger.exception("[dispatch] step '%s' failed for project '%s': %s", step, project_id, e)
            _move(project_id, PlankaColumn.FAILED, move_card_fn)
            if sink:
                sink.post_comment(
                    project_id,
                    f"**[ERROR] step `{step}` 失敗 — 已移至 Failed**\n\n```\n{type(e).__name__}: {e}\n```",
                )
            write_error_report(project_id, e, str(step), db_url)
            return

        # Stop if a HITL pause was triggered (e.g. plan_approval)
        refreshed = get_project(project_id, db_url)
        if refreshed and (refreshed.get("config") or {}).get("paused_at"):
            logger.info("[workflow][dispatch] HITL pause — stopping loop for project '%s'.", project_id)
            return


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------

def _run_plan(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][plan] START  project='%s'", project_id)
    updates = plan_step(state)
    _merge_state(project_id, updates, db_url)

    if updates.get("needs_human_approval"):
        merge_config(project_id, {"paused_at": "plan_approval"}, db_url)
        _move(project_id, PlankaColumn.REVIEW, move_card_fn)
        if sink:
            plan = updates.get("implementation_plan") or {}
            preview = json.dumps(plan, ensure_ascii=False, indent=2)[:_PLAN_PREVIEW_MAX_CHARS]
            sink.post_comment(
                project_id,
                f"**計畫待審核**\n\n```json\n{preview}\n```\n\n"
                "確認後將卡片移回 **Executing** 繼續，或留在 **Review** 暫停。",
            )
        logger.info("[workflow][plan] END HITL pause — moved to Review  project='%s'", project_id)
        return

    if sink:
        _upload_plan_artifacts(project_id, sink)

    set_workflow_step(project_id, WorkflowStep.IMPLEMENT, db_url)
    logger.info("[workflow][plan] END → implement  project='%s'", project_id)


def _run_implement(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][implement] START  project='%s'", project_id)

    if state.get("paused_at") == "plan_approval":
        merge_config(project_id, {"paused_at": None}, db_url)
        logger.info("[implement] resuming after plan approval  project='%s'", project_id)

    if os.getenv("BACKTEST_MODE", "mock") != "mock":
        spec = state.get("spec") or {}
        if not (spec.get("trading_scope") or spec.get("universe")):
            raise RuntimeError(
                f"Real backtest for project '{project_id}' requires a completed spec review — "
                f"spec is missing 'trading_scope'. Complete spec review first or set BACKTEST_MODE=mock."
            )

    if not state.get("implementation_plan"):
        logger.warning("[implement] implementation_plan missing — re-running plan step  project='%s'", project_id)
        plan_updates = plan_step(state)
        _merge_state(project_id, plan_updates, db_url)
        state = {**state, **plan_updates}

    prev_artifact_paths = {a.get("path") for a in state.get("artifacts", [])}
    updates = implement_step(state)
    _merge_state(project_id, updates, db_url)

    if sink:
        _upload_new_artifacts(project_id, updates.get("artifacts", []), prev_artifact_paths, sink)

    set_workflow_step(project_id, WorkflowStep.TEST, db_url)
    logger.info("[workflow][implement] END → test  project='%s'", project_id)


def _run_test(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][test] START  project='%s'", project_id)
    updates = test_step(state)
    _merge_state(project_id, updates, db_url)
    set_workflow_step(project_id, WorkflowStep.ANALYZE, db_url)
    logger.info("[workflow][test] END → analyze  project='%s'", project_id)


def _run_analyze(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][analyze] START  project='%s'", project_id)
    updates = analyze_step(state)

    # analyze_step returns only last_result and last_reason; all other values come from state.
    result = updates.get("last_result", state.get("last_result", AnalysisResult.UNKNOWN))
    reason = updates.get("last_reason", state.get("last_reason", ""))
    analyze_attempt = state.get("analyze_attempt", 0) + 1
    max_loops = state.get("max_loops", 3)

    if result != AnalysisResult.PASS and analyze_attempt >= max_loops:
        result = AnalysisResult.TERMINATE
        reason = f"EXHAUSTED: {max_loops} loops without meeting criteria."

    final_updates = {**updates, "last_result": result, "last_reason": reason, "analyze_attempt": analyze_attempt}
    _merge_state(project_id, final_updates, db_url)

    logger.info("[workflow][analyze] END result=%s attempt=%d/%d  project='%s'", result, analyze_attempt, max_loops, project_id)

    record_loop_metrics(
        project_id=project_id, loop_index=analyze_attempt,
        result=result, reason=reason,
        metrics=state.get("test_metrics", {}), db_url=db_url,
    )

    # Pack this iteration's artifacts (named v{N}_*) into v{N}_backtest.zip and upload
    if sink:
        n = analyze_attempt - 1  # 0-indexed iteration number written during implement
        prefix = f"v{n}_"
        iter_arts = [
            a for a in state.get("artifacts", [])
            if Path(a.get("path", "")).name.startswith(prefix)
        ]
        _upload_iteration_zip(project_id, iter_arts, n, sink)

    set_workflow_step(project_id, _ANALYZE_NEXT_STEP.get(result, WorkflowStep.TERMINATE), db_url)


def _run_summarize(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][summarize] START  project='%s'", project_id)
    updates = summarize_step(state)
    _merge_state(project_id, updates, db_url)

    if sink:
        # summarize_step appends to artifacts, so we need the merged view for the artifact list.
        merged_artifacts = updates.get("artifacts") or state.get("artifacts") or []
        summary_art = next((a for a in reversed(merged_artifacts) if a.get("type") == "summary"), None)
        if summary_art:
            path = summary_art.get("path", "")
            analyze_attempt = state.get("analyze_attempt", 0)
            ts = datetime.now().strftime("%Y%m%d%H%M")
            filename = f"v{analyze_attempt}_researchsummary_{ts}.md"
            try:
                content = Path(path).read_text(encoding="utf-8")
                card_id = sink.resolve_card_id(project_id)
                if card_id:
                    sink.upload_spec_attachment(card_id, filename, content)
                    logger.info("[summarize] uploaded '%s' to Planka  project='%s'", filename, project_id)
                    Path(path).unlink(missing_ok=True)
                else:
                    logger.warning("[summarize] card not found; local file preserved: %s", path)
            except Exception as e:
                logger.warning("[summarize] upload failed: %s", e)

    set_workflow_step(project_id, WorkflowStep.DONE, db_url)
    _move(project_id, PlankaColumn.DONE, move_card_fn)
    logger.info("[workflow][summarize] END → Done  project='%s'", project_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload_iteration_zip(
    project_id: str,
    artifacts: list[dict],
    iteration: int,
    sink: WorkflowSink,
) -> None:
    """Pack artifacts from one iteration into v{N}_backtest.zip and upload."""
    card_id = sink.resolve_card_id(project_id)
    if not card_id:
        return
    paths = [Path(a["path"]) for a in artifacts if a.get("path") and Path(a["path"]).exists()]
    if not paths:
        return
    zip_name = f"v{iteration}_backtest.zip"
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                zf.write(p, p.name)
        sink.upload_bytes_attachment(card_id, zip_name, buf.getvalue(), "application/zip")
        logger.info("[analyze] uploaded '%s' (%d files)  project='%s'", zip_name, len(paths), project_id)
    except Exception as e:
        logger.warning("[analyze] zip upload failed for '%s': %s", zip_name, e)


def _upload_plan_artifacts(project_id: str, sink: WorkflowSink) -> None:
    """Upload plan_output.json to Planka if it exists."""
    from app.freqtrade.steps import ARTIFACTS_DIR
    plan_file = ARTIFACTS_DIR / "plan_output.json"
    if not plan_file.exists():
        return
    card_id = sink.resolve_card_id(project_id)
    if not card_id:
        return
    try:
        content = plan_file.read_text(encoding="utf-8")
        ts = datetime.now().strftime("%Y%m%d%H%M")
        sink.upload_spec_attachment(card_id, f"plan_output_{ts}.json", content)
        logger.info("[plan] uploaded plan_output.json to Planka  project='%s'", project_id)
    except Exception as e:
        logger.warning("[plan] plan_output.json upload failed: %s", e)


_BACKTEST_BUNDLED_TYPES = frozenset({
    "is_zip", "oos_zip", "is_result", "oos_result", "trades", "signals", "report",
})


def _upload_new_artifacts(
    project_id: str,
    artifacts: list[dict],
    prev_paths: set[str],
    sink: WorkflowSink,
) -> None:
    """Upload artifacts that are new (not in prev_paths) to Planka.

    Artifacts whose ``type`` is bundled into ``v{N}_backtest.zip`` (uploaded by
    ``_upload_iteration_zip`` in ``_run_analyze``) are skipped here to avoid
    duplicate Planka attachments.
    """
    card_id = sink.resolve_card_id(project_id)
    if not card_id:
        return
    for art in artifacts:
        art_type = art.get("type", "")
        if art_type in _BACKTEST_BUNDLED_TYPES:
            continue
        path_str = art.get("path", "")
        if not path_str or path_str in prev_paths:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            if p.suffix == ".zip":
                sink.upload_bytes_attachment(card_id, p.name, p.read_bytes(), "application/zip")
            else:
                sink.upload_spec_attachment(card_id, p.name, p.read_text(encoding="utf-8"))
            logger.info("[implement] uploaded '%s' to Planka  project='%s'", p.name, project_id)
        except Exception as e:
            logger.warning("[implement] upload failed for '%s': %s", p.name, e)


def _build_state(project: dict) -> dict:
    cfg = project.get("config") or {}
    spec = cfg.get("spec") or {}
    # Three distinct loop counters — keep them separate:
    #   loop_index     : outer strategy iteration; incremented by summarize_step after each
    #                    complete plan→summarize cycle; used for artifact file naming
    #                    (loop_0_train.json, loop_1_train.json …). Managed by steps.py.
    #   analyze_attempt: cumulative count of _run_analyze calls across the project lifetime;
    #                    written to loop_metrics.loop_index in the DB. Managed here.
    #   attempt_count  : within-loop OOS attempt counter; reset to 0 by summarize_step.
    #                    Managed by steps.py.
    cfg_max_loops_raw = cfg.get("max_loops")
    max_loops = int(cfg_max_loops_raw or 3)
    logger.info(
        "[_build_state] max_loops cfg_raw=%r resolved=%d  project='%s'",
        cfg_max_loops_raw, max_loops, project.get("id"),
    )
    # Surface the locked revise_pipeline_version (if any) into state so
    # `_run_revise` can detect "already locked" without a separate DB read,
    # and so revise_step's _resolve_version honours the project lock.
    revise_pipeline_version = cfg.get("revise_pipeline_version")
    logger.info(
        "[_build_state] revise_pipeline_version=%r",
        revise_pipeline_version,
    )
    return {
        "project_id":             project["id"],
        "loop_index":             cfg.get("loop_index", 0),
        "loop_goal":              spec.get("hypothesis") or project.get("goal") or "research",
        "spec":                   spec,
        "implementation_plan":    cfg.get("implementation_plan"),
        "last_result":            cfg.get("last_result", AnalysisResult.UNKNOWN),
        "last_reason":            cfg.get("last_reason", ""),
        "max_loops":              max_loops,
        "analyze_attempt":        cfg.get("analyze_attempt", 0),
        "needs_human_approval":   False,
        "attempt_count":          cfg.get("attempt_count", 0),
        "paused_at":              cfg.get("paused_at"),
        "is_metrics":             cfg.get("is_metrics", {}),
        "oos_metrics":            cfg.get("oos_metrics", {}),
        "test_metrics":           cfg.get("test_metrics", {}),
        "artifacts":              cfg.get("artifacts", []),
        "revise_pipeline_version": revise_pipeline_version,
    }


def _merge_state(project_id: str, updates: dict, db_url: str | None = None) -> None:
    """Persist node updates back to projects.config."""
    if not updates:
        return
    patch = {k: v for k, v in updates.items() if k not in _MERGE_EXCLUDED}
    if patch:
        merge_config(project_id, patch, db_url)


def _move(project_id: str, column: PlankaColumn | str, move_card_fn: MoveCardFn) -> None:
    """Move the Planka card to a named column. No-op if move_card_fn is None."""
    if move_card_fn is None:
        return
    try:
        move_card_fn(project_id, column)
    except Exception as e:
        logger.warning("[dispatch] _move '%s' failed: %s", column, e)


def _run_revise(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][revise] START  project='%s'", project_id)

    # §10.1 / §10.2 — Resolve and lock REVISE_PIPELINE_VERSION on first revise entry.
    # Per spec `fail-path-revise-plan` and design D12: a project's revise rounds
    # must use a single pipeline version for its entire lifetime. The first time
    # we dispatch revise for this project (no value in state, hence none in DB),
    # we read the env var, normalise it, persist it via merge_config, and
    # propagate it into the in-flight state so revise_step picks it up. If the
    # state already has a value, the lock wins regardless of the current env var.
    if state.get("revise_pipeline_version") is None:
        from app.freqtrade.steps.revise import _resolve_version
        # Detect non-empty invalid env values for the warning the spec requires.
        raw_env = os.getenv("REVISE_PIPELINE_VERSION")
        if raw_env and raw_env not in ("v1", "v2"):
            logger.warning(
                "[revise] REVISE_PIPELINE_VERSION=%r is invalid; falling back to v1",
                raw_env,
            )
        # _resolve_version already handles unset/invalid → "v1" with warning.
        resolved = _resolve_version(state)
        merge_config(project_id, {"revise_pipeline_version": resolved}, db_url)
        state["revise_pipeline_version"] = resolved
        logger.info(
            "[revise] flag resolved=%s, locked into project config",
            resolved,
        )

    prev_artifact_paths = {a.get("path") for a in state.get("artifacts", [])}
    updates = revise_step(state)
    new_artifacts = updates.get("artifacts", [])

    if updates.get("last_result") == "TERMINATE":
        _merge_state(project_id, updates, db_url)
        # Upload diagnostic artifacts produced by the TERMINATE path
        # (v{N}_audit.md, partial revised_direction.md if Stage A succeeded)
        # so reviewers can inspect failure mode in Planka.
        if sink:
            _upload_new_artifacts(project_id, new_artifacts, prev_artifact_paths, sink)
        set_workflow_step(project_id, WorkflowStep.TERMINATE, db_url)
        logger.info("[workflow][revise] END TERMINATE signal from revise_step  project='%s'", project_id)
        return

    _merge_state(project_id, updates, db_url)

    if sink:
        # Upload all new artifacts (revised_direction, audit, strategy_spec).
        # The §7 filter in _upload_new_artifacts skips bundled-backtest types
        # but lets these markdown-class artifacts pass through.
        _upload_new_artifacts(project_id, new_artifacts, prev_artifact_paths, sink)

    set_workflow_step(project_id, WorkflowStep.IMPLEMENT, db_url)
    logger.info("[workflow][revise] END → implement  project='%s'", project_id)


def _run_terminate_summarize(
    project_id: str, state: dict,
    db_url: str | None = None, sink: WorkflowSink | None = None, move_card_fn: MoveCardFn = None,
) -> None:
    logger.info("[workflow][terminate] START  project='%s'", project_id)
    updates = terminate_summarize_step(state)
    _merge_state(project_id, updates, db_url)

    if sink:
        analyze_attempt = state.get("analyze_attempt", 1)
        last_iter = max(analyze_attempt - 1, 0)
        filename = f"v0_{last_iter}_summary_report.md"
        term_art = next(
            (a for a in reversed(updates.get("artifacts", []))
             if a.get("type") == "terminate_summary"),
            None,
        )
        if term_art:
            p = Path(term_art["path"])
            card_id = sink.resolve_card_id(project_id)
            if card_id and p.exists():
                try:
                    sink.upload_spec_attachment(card_id, filename, p.read_text(encoding="utf-8"))
                    logger.info("[terminate] uploaded '%s'  project='%s'", filename, project_id)
                    p.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning("[terminate] upload '%s' failed: %s", filename, e)
        sink.post_comment(
            project_id,
            f"**Research 終止 — 綜合報告已產出**\n\n"
            f"報告：`{filename}`\n\n"
            f"原因：{state.get('last_reason', '')[:300]}",
        )

    _move(project_id, PlankaColumn.REVIEW, move_card_fn)
    set_workflow_step(project_id, WorkflowStep.DONE, db_url)
    logger.info("[workflow][terminate] END  project='%s'", project_id)


_STEP_HANDLERS: dict[WorkflowStep, _StepHandler] = {
    WorkflowStep.PLAN:      _run_plan,
    WorkflowStep.IMPLEMENT: _run_implement,
    WorkflowStep.TEST:      _run_test,
    WorkflowStep.ANALYZE:   _run_analyze,
    WorkflowStep.REVISE:    _run_revise,
    WorkflowStep.SUMMARIZE: _run_summarize,
    WorkflowStep.TERMINATE: _run_terminate_summarize,
}
