"""v2 revise pipeline — intent → checklist → subagent → audit (5 stages).

Implements the openspec capability ``fail-path-revise-plan`` MODIFIED requirement
"REVISE step 執行多階段修訂流程" plus the related ADDED requirements (locked
checklist, retry caps, feature flag honouring).

Layout:
- :class:`ReviseTerminate` — internal signal for TERMINATE-class failures
- :class:`StageHistory` — accumulates per-attempt log used to render
  ``v{N}_audit.md`` on either TERMINATE or APPROVED exit
- ``_stage_a_intent`` / ``_stage_b_audit_intent`` / ``_stage_c_checklist`` /
  ``_stage_d_subagent`` / ``_stage_e_audit`` — pure-ish stage helpers
- ``_promote`` — atomic move of the audited candidate into ``artifacts/strategies/``
- ``revise_step_v2`` — top-level orchestrator with 3 independent retry counters
  plus the dishonest-streak detector

Counter semantics (per spec ``fail-path-revise-plan`` and design D11):
- ``intent_retry``     0..2  — Stage B REJECTED rounds
- ``checklist_retry``  0..2  — UNIMPLEMENTABLE_CHECKLIST or CHECKLIST_AMBIGUOUS rounds
- ``subagent_retry``   0..2  — IMPLEMENTATION_FAILED rounds; resets when a new
                                checklist is produced
Any counter reaching 3 = exhausted = TERMINATE. Two consecutive
``dishonest_attempt`` rounds also TERMINATE regardless of other counters.
The continuity counter resets when a new checklist is produced (per spec).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.freqtrade.audit import run_audit, write_audit_report
from app.freqtrade.checklist import (
    AuditReport,
    Checklist,
    CompletionReport,
    Overall,
    RoutingDecision,
    ValidationError,
    cross_check,
    parse_checklist,
    parse_completion_report,
)

from .._common import ARTIFACTS_DIR, _call_llm as _default_call_llm, _load_prompt
from ..strategy_snapshot import write_strategy_spec_snapshot

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # counter values 0,1,2 → 3 total attempts before TERMINATE


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class ReviseTerminate(Exception):
    """Internal signal: terminate the v2 revise loop with a reason code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass
class StageHistory:
    """Accumulator for per-attempt events; rendered into ``v{N}_audit.md``."""

    events: list[str] = field(default_factory=list)
    final_verdict: str | None = None

    def log(self, msg: str) -> None:
        logger.info("[workflow][revise[v2]] %s", msg)
        self.events.append(msg)


# ---------------------------------------------------------------------------
# Stage A — LLM1 proposes intent
# ---------------------------------------------------------------------------


def _stage_a_intent(
    *,
    state: dict,
    staging_dir: Path,
    attempt: int,
    prev_audit_feedback: str | None,
    call_llm: Callable[..., str],
) -> Path:
    """Run LLM1 to produce ``revision_intent.md`` for this attempt.

    Writes the intent to ``{staging_dir}/intent_attempt_{attempt}.md`` (LLM
    wrote ``revision_intent.md`` in cwd, then we copy it to the persistent
    name). Returns the path to the persisted artifact.
    """
    plan = state.get("implementation_plan", {}) or {}
    reason = state.get("last_reason", "")
    old_py_path = plan.get("strategy_file")
    old_py_text = ""
    if old_py_path and Path(old_py_path).exists():
        old_py_text = Path(old_py_path).read_text(encoding="utf-8")

    cwd = staging_dir / f"_llm_io_intent_{attempt}"
    cwd.mkdir(parents=True, exist_ok=True)

    prompt = _load_prompt("revise_intent").format(
        REASON=reason,
        PARAMS=json.dumps(plan, ensure_ascii=False, indent=2),
        OLD_PY=old_py_text,
        ATTEMPT_COUNT=attempt,
        OUTPUT_DIR=str(cwd),
    )
    if prev_audit_feedback:
        prompt += (
            "\n\n## 上一次審核回饋（請參考並修正修訂方向）\n\n"
            + prev_audit_feedback
        )

    call_llm(prompt, cwd=str(cwd))

    src = cwd / "revision_intent.md"
    if not src.exists():
        raise ReviseTerminate(
            "INTENT_LLM_NO_OUTPUT",
            detail=f"Stage A attempt {attempt}: LLM1 did not produce revision_intent.md",
        )

    # Check for explicit TERMINATE signal from LLM1
    status_file = cwd / "revision_intent_status.txt"
    if status_file.exists():
        status = status_file.read_text(encoding="utf-8").strip().splitlines()[0:1]
        if status and status[0].strip().upper() == "TERMINATE":
            raise ReviseTerminate(
                "INTENT_GAVE_UP",
                detail=f"Stage A attempt {attempt}: LLM1 returned TERMINATE — no further revision viable",
            )

    persisted = staging_dir / f"intent_attempt_{attempt}.md"
    persisted.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return persisted


# ---------------------------------------------------------------------------
# Stage B — LLM2 audits intent
# ---------------------------------------------------------------------------


def _stage_b_audit_intent(
    *,
    intent_path: Path,
    state: dict,
    staging_dir: Path,
    attempt: int,
    call_llm: Callable[..., str],
) -> tuple[bool, str]:
    """Run LLM2 to audit the intent. Returns ``(approved, feedback)``.

    ``feedback`` is empty when approved; on REJECTED, it contains the
    LLM2-written feedback markdown to be re-injected into Stage A.
    """
    cwd = staging_dir / f"_llm_io_intent_audit_{attempt}"
    cwd.mkdir(parents=True, exist_ok=True)

    intent_md = intent_path.read_text(encoding="utf-8")
    prompt = _load_prompt("revise_intent_audit").format(
        REASON=state.get("last_reason", ""),
        INTENT_MD=intent_md,
        OUTPUT_DIR=str(cwd),
    )
    call_llm(prompt, cwd=str(cwd))

    result_file = cwd / "revision_intent_audit_result.txt"
    if not result_file.exists():
        raise ReviseTerminate(
            "INTENT_AUDIT_LLM_NO_OUTPUT",
            detail=f"Stage B attempt {attempt}: LLM2 did not produce result file",
        )
    lines = result_file.read_text(encoding="utf-8").strip().splitlines()
    verdict = lines[0].strip().upper() if lines else ""
    if verdict == "APPROVED":
        return True, ""
    if verdict == "REJECTED":
        feedback_file = cwd / "revision_intent_audit_feedback.md"
        feedback = feedback_file.read_text(encoding="utf-8") if feedback_file.exists() else "\n".join(lines[1:])
        return False, feedback
    raise ReviseTerminate(
        "INTENT_AUDIT_BAD_VERDICT",
        detail=f"Stage B attempt {attempt}: unexpected verdict line {verdict!r}",
    )


# ---------------------------------------------------------------------------
# Stage C — LLM2 produces checklist.yaml
# ---------------------------------------------------------------------------


def _stage_c_checklist(
    *,
    intent_path: Path,
    state: dict,
    staging_dir: Path,
    iteration: int,
    attempt: int,
    prev_feedback: str | None,
    call_llm: Callable[..., str],
) -> tuple[Checklist, Path]:
    """Run LLM2 to translate intent → checklist.yaml. Returns (checklist, path).

    Writes the persisted artifact to
    ``{staging_dir}/checklist_attempt_{attempt}.yaml`` (per spec; never overwrites).
    """
    plan = state.get("implementation_plan", {}) or {}
    old_py_path = plan.get("strategy_file")
    old_py_text = Path(old_py_path).read_text(encoding="utf-8") if old_py_path and Path(old_py_path).exists() else ""

    cwd = staging_dir / f"_llm_io_checklist_{attempt}"
    cwd.mkdir(parents=True, exist_ok=True)

    prompt = _load_prompt("revise_checklist").format(
        ITERATION=iteration,
        INTENT_MD=intent_path.read_text(encoding="utf-8"),
        OLD_PY=old_py_text,
        PARAMS=json.dumps(plan, ensure_ascii=False, indent=2),
        OUTPUT_DIR=str(cwd),
    )
    if prev_feedback:
        prompt += (
            "\n\n## 上一次 checklist 失敗的原因（請改進）\n\n"
            + prev_feedback
        )

    call_llm(prompt, cwd=str(cwd))

    src = cwd / "checklist.yaml"
    if not src.exists():
        raise ValidationError(f"Stage C attempt {attempt}: LLM2 did not produce checklist.yaml")

    yaml_str = src.read_text(encoding="utf-8")
    checklist = parse_checklist(yaml_str)  # raises ValidationError on schema fail

    persisted = staging_dir / f"checklist_attempt_{attempt}.yaml"
    persisted.write_text(yaml_str, encoding="utf-8")
    return checklist, persisted


# ---------------------------------------------------------------------------
# Stage D — Subagent writes new .py + completion_report
# ---------------------------------------------------------------------------


def _stage_d_subagent(
    *,
    checklist: Checklist,
    checklist_path: Path,
    state: dict,
    staging_dir: Path,
    iteration: int,
    attempt: int,
    prev_audit_feedback: str | None,
    call_llm: Callable[..., str],
) -> tuple[Path, CompletionReport]:
    """Run subagent to produce candidate.py + completion_report.

    Writes:
      - ``{staging_dir}/candidate.py`` (overwritten on retry; this is the
        path Stage E reads and that ``_promote`` moves on APPROVED)
      - ``{staging_dir}/completion_report_attempt_{attempt}.yaml`` (persistent;
        never overwritten)
    """
    plan = state.get("implementation_plan", {}) or {}
    old_py_path = plan.get("strategy_file")
    old_py_text = Path(old_py_path).read_text(encoding="utf-8") if old_py_path and Path(old_py_path).exists() else ""

    cwd = staging_dir / f"_llm_io_subagent_{attempt}"
    cwd.mkdir(parents=True, exist_ok=True)

    candidate_path = staging_dir / "candidate.py"
    completion_persisted = staging_dir / f"completion_report_attempt_{attempt}.yaml"
    # subagent writes to the LLM cwd; we copy out after.
    completion_in_cwd = cwd / "completion_report.yaml"

    prompt = _load_prompt("revise_subagent").format(
        CHECKLIST_YAML=checklist_path.read_text(encoding="utf-8"),
        OLD_PY=old_py_text,
        CANDIDATE_PY_PATH=str(candidate_path),
        COMPLETION_REPORT_PATH=str(completion_in_cwd),
        ITERATION=iteration,
        ATTEMPT=attempt,
    )
    if prev_audit_feedback:
        prompt += (
            "\n\n## 上一次 audit 失敗的原因（請更嚴謹自查）\n\n"
            + prev_audit_feedback
        )

    call_llm(prompt, cwd=str(cwd))

    if not candidate_path.exists():
        raise ReviseTerminate(
            "SUBAGENT_LLM_NO_OUTPUT",
            detail=f"Stage D attempt {attempt}: subagent did not write {candidate_path}",
        )
    if not completion_in_cwd.exists():
        raise ReviseTerminate(
            "SUBAGENT_LLM_NO_OUTPUT",
            detail=f"Stage D attempt {attempt}: subagent did not write completion_report.yaml",
        )

    completion_yaml = completion_in_cwd.read_text(encoding="utf-8")
    completion_persisted.write_text(completion_yaml, encoding="utf-8")
    completion_report = parse_completion_report(completion_yaml)
    return candidate_path, completion_report


# ---------------------------------------------------------------------------
# Stage E — audit (deterministic + LLM3); thin wrapper around audit.run_audit
# ---------------------------------------------------------------------------


def _stage_e_audit(
    *,
    checklist: Checklist,
    candidate_path: Path,
    state: dict,
    completion_report: CompletionReport,
    staging_dir: Path,
    attempt: int,
    call_llm: Callable[..., str],
) -> AuditReport:
    """Run the audit and persist ``audit_report_attempt_{attempt}.yaml``."""
    plan = state.get("implementation_plan", {}) or {}
    old_py_path = plan.get("strategy_file")
    if not old_py_path:
        raise ReviseTerminate(
            "MISSING_OLD_PY",
            detail="Stage E: plan.strategy_file is empty; cannot run audit against baseline",
        )
    cwd = staging_dir / f"_llm_io_audit_{attempt}"
    cwd.mkdir(parents=True, exist_ok=True)

    report = run_audit(
        checklist=checklist,
        new_py_path=candidate_path,
        old_py_path=Path(old_py_path),
        completion_report=completion_report,
        output_dir=str(cwd),
        iteration=checklist.iteration,
        attempt=attempt,
        call_llm=call_llm,
    )
    write_audit_report(report, staging_dir / f"audit_report_attempt_{attempt}.yaml")
    return report


# ---------------------------------------------------------------------------
# Promote: atomic move staging → artifacts/strategies/v{N}/
# ---------------------------------------------------------------------------


def _promote(
    *, candidate_path: Path, target_dir: Path, strategy_name: str,
) -> Path:
    """Atomically promote the candidate into ``target_dir``.

    Uses a tmp-then-rename pattern within ``target_dir.parent`` so that, even
    if the move is interrupted, ``target_dir`` either fully exists or doesn't.
    Returns the final ``.py`` path.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{strategy_name}.py"
    tmp_path = target_dir / f".{strategy_name}.py.partial"

    try:
        shutil.copy2(candidate_path, tmp_path)
        os.replace(tmp_path, final_path)  # atomic on POSIX & Windows for same volume
    except OSError as e:
        # cleanup partial then re-raise as ReviseTerminate so orchestrator
        # records this as a failure mode rather than crashing dispatch
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        if final_path.exists() and not final_path.stat().st_size:
            try:
                final_path.unlink()
            except OSError:
                pass
        raise ReviseTerminate(
            "PROMOTE_FAILED",
            detail=f"failed to promote candidate to {final_path}: {e}",
        )
    return final_path


# ---------------------------------------------------------------------------
# v{N}_revised_direction.md and v{N}_audit.md writers
# ---------------------------------------------------------------------------


def _write_revised_direction(intent_path: Path, output_path: Path) -> Path:
    """Copy the approved intent to ``v{N}_revised_direction.md`` for Planka upload."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(intent_path.read_text(encoding="utf-8"), encoding="utf-8")
    return output_path


def _write_audit_log_md(
    *,
    history: StageHistory,
    counters: dict[str, int],
    iteration: int,
    output_path: Path,
) -> Path:
    """Render ``v{N}_audit.md`` summarising counters + per-attempt events."""
    lines = [f"# Revise v2 Audit Log — iteration {iteration}\n\n"]
    lines.append("## 最終結果\n\n")
    lines.append(f"- {history.final_verdict or 'UNKNOWN'}\n\n")
    lines.append("## Retry counters\n\n")
    for k in ("intent_retry", "checklist_retry", "subagent_retry", "dishonest_streak"):
        lines.append(f"- {k}: {counters.get(k, 0)}\n")
    lines.append("\n## Stage 事件記錄\n\n")
    for ev in history.events:
        lines.append(f"- {ev}\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def revise_step_v2(state: dict, *, call_llm: Callable[..., str] | None = None) -> dict:
    """v2 revise orchestrator.

    Reads ``state["implementation_plan"]`` + ``state["last_reason"]`` and
    drives the 5-stage pipeline. Returns a partial state update suitable for
    merging into ``projects.config``:

    - On APPROVED: ``implementation_plan.strategy_file`` is updated, artifacts
      list extended with ``revised_direction``, ``audit``, ``strategy_spec``
      (the last to be added in §4); ``last_result = "REVISED"``.
    - On TERMINATE: ``last_result = "TERMINATE"``, ``last_reason`` describes
      the failure mode; staging is preserved untouched.
    """
    if call_llm is None:
        call_llm = _default_call_llm

    iteration = state.get("analyze_attempt", 0)
    staging_dir = ARTIFACTS_DIR / ".staging" / f"v{iteration}"
    staging_dir.mkdir(parents=True, exist_ok=True)

    history = StageHistory()
    history.log(f"orchestrator START iteration={iteration}")

    counters = {
        "intent_retry": 0,
        "checklist_retry": 0,
        "subagent_retry": 0,
        "dishonest_streak": 0,
    }

    artifacts: list[dict] = list(state.get("artifacts") or [])

    audit_log_path = ARTIFACTS_DIR / f"v{iteration}_audit.md"
    direction_path = ARTIFACTS_DIR / f"v{iteration}_revised_direction.md"

    def _terminate(reason: str, detail: str = "") -> dict:
        history.log(f"orchestrator END verdict=TERMINATE reason={reason} detail={detail}")
        history.final_verdict = f"TERMINATE — {reason}"
        _write_audit_log_md(
            history=history, counters=counters, iteration=iteration,
            output_path=audit_log_path,
        )
        artifacts.append({"type": "audit", "path": str(audit_log_path)})
        return {
            "last_result": "TERMINATE",
            "last_reason": f"v2 revise terminated: {reason} — {detail}".strip(" —"),
            "artifacts": artifacts,
        }

    # ------ Stage A + B: intent loop ------
    intent_path: Path | None = None
    audit_feedback: str | None = None
    while counters["intent_retry"] <= MAX_RETRIES:
        attempt = counters["intent_retry"]
        history.log(f"Stage A attempt={attempt} START")
        try:
            intent_path = _stage_a_intent(
                state=state, staging_dir=staging_dir,
                attempt=attempt, prev_audit_feedback=audit_feedback,
                call_llm=call_llm,
            )
        except ReviseTerminate as e:
            return _terminate(e.reason, e.detail)
        except Exception as e:  # subprocess error / IO failure / etc.
            return _terminate("STAGE_A_LLM_FAILED", detail=f"{type(e).__name__}: {e}")
        history.log(f"Stage A attempt={attempt} END intent={intent_path.name}")

        history.log(f"Stage B attempt={attempt} START")
        try:
            approved, feedback = _stage_b_audit_intent(
                intent_path=intent_path, state=state,
                staging_dir=staging_dir, attempt=attempt, call_llm=call_llm,
            )
        except ReviseTerminate as e:
            return _terminate(e.reason, e.detail)
        except Exception as e:
            return _terminate("STAGE_B_LLM_FAILED", detail=f"{type(e).__name__}: {e}")
        history.log(f"Stage B attempt={attempt} END verdict={'APPROVED' if approved else 'REJECTED'}")

        if approved:
            break
        audit_feedback = feedback
        counters["intent_retry"] += 1
    else:
        return _terminate("INTENT_RETRY_EXHAUSTED")

    # write revised_direction artifact (per spec, after Stage B APPROVED)
    _write_revised_direction(intent_path, direction_path)
    artifacts.append({"type": "revised_direction", "path": str(direction_path)})

    # ------ Stage C/D/E loop ------
    checklist_feedback: str | None = None
    last_audit_report: AuditReport | None = None
    promoted_path: Path | None = None

    while counters["checklist_retry"] <= MAX_RETRIES:
        ck_attempt = counters["checklist_retry"]
        history.log(f"Stage C attempt={ck_attempt} START")
        try:
            checklist, checklist_path = _stage_c_checklist(
                intent_path=intent_path, state=state,
                staging_dir=staging_dir, iteration=iteration,
                attempt=ck_attempt, prev_feedback=checklist_feedback,
                call_llm=call_llm,
            )
        except ValidationError as e:
            history.log(f"Stage C attempt={ck_attempt} END schema_FAIL: {e}")
            counters["checklist_retry"] += 1
            checklist_feedback = f"上一份 checklist schema 不合法：{e}"
            continue
        except ReviseTerminate as e:
            return _terminate(e.reason, e.detail)
        except Exception as e:
            return _terminate("STAGE_C_LLM_FAILED", detail=f"{type(e).__name__}: {e}")
        history.log(f"Stage C attempt={ck_attempt} END items={len(checklist.items)}")

        # subagent_retry resets on each new checklist (per spec)
        counters["subagent_retry"] = 0
        # dishonest streak resets when checklist changes (per spec)
        counters["dishonest_streak"] = 0
        subagent_feedback: str | None = None
        need_new_checklist = False

        while counters["subagent_retry"] <= MAX_RETRIES:
            sa_attempt = counters["subagent_retry"]
            history.log(
                f"Stage D ck_attempt={ck_attempt} sa_attempt={sa_attempt} START"
            )
            try:
                candidate_path, completion_report = _stage_d_subagent(
                    checklist=checklist, checklist_path=checklist_path,
                    state=state, staging_dir=staging_dir,
                    iteration=iteration, attempt=sa_attempt,
                    prev_audit_feedback=subagent_feedback,
                    call_llm=call_llm,
                )
            except ReviseTerminate as e:
                return _terminate(e.reason, e.detail)
            except Exception as e:
                return _terminate(
                    "STAGE_D_LLM_FAILED", detail=f"{type(e).__name__}: {e}",
                )

            decision = cross_check(checklist, completion_report)
            history.log(
                f"Stage D ck_attempt={ck_attempt} sa_attempt={sa_attempt} END "
                f"decision={decision.value}"
            )
            if decision == RoutingDecision.UNIMPLEMENTABLE_CHECKLIST:
                # subagent says checklist itself is broken → back to Stage C
                checklist_feedback = (
                    f"subagent 在 attempt={sa_attempt} 標記 unimplementable_items="
                    f"{completion_report.unimplementable_items}"
                )
                history.log("→ UNIMPLEMENTABLE_CHECKLIST: requesting new checklist")
                need_new_checklist = True
                break
            if decision == RoutingDecision.IMPLEMENTATION_FAILED:
                # subagent self-reported some incomplete (without unimplementable) → retry Stage D
                history.log(
                    f"→ IMPLEMENTATION_FAILED (self-report incomplete) — failed_items="
                    f"{completion_report.failed_items()}"
                )
                subagent_feedback = (
                    f"上一輪你自報以下 items 未完成：{completion_report.failed_items()}"
                )
                counters["subagent_retry"] += 1
                continue
            # READY_FOR_AUDIT
            history.log(
                f"Stage E ck_attempt={ck_attempt} sa_attempt={sa_attempt} START"
            )
            try:
                audit_report = _stage_e_audit(
                    checklist=checklist, candidate_path=candidate_path,
                    state=state, completion_report=completion_report,
                    staging_dir=staging_dir, attempt=sa_attempt,
                    call_llm=call_llm,
                )
            except ReviseTerminate as e:
                return _terminate(e.reason, e.detail)
            except Exception as e:
                return _terminate(
                    "STAGE_E_FAILED", detail=f"{type(e).__name__}: {e}",
                )
            history.log(
                f"Stage E ck_attempt={ck_attempt} sa_attempt={sa_attempt} END "
                f"overall={audit_report.overall.value}"
                + (f" reject_summary={audit_report.reject_summary!r}"
                   if audit_report.reject_summary else "")
            )

            last_audit_report = audit_report

            # dishonest streak tracking (resets on new checklist; tracked here)
            if audit_report.has_dishonest_attempt():
                counters["dishonest_streak"] += 1
                history.log(
                    f"dishonest_attempt detected; streak={counters['dishonest_streak']}"
                )
                if counters["dishonest_streak"] >= 2:
                    return _terminate(
                        "SUBAGENT_DISHONEST",
                        detail=f"two consecutive dishonest_attempts within ck_attempt={ck_attempt}",
                    )
            else:
                counters["dishonest_streak"] = 0

            if audit_report.overall == Overall.APPROVED:
                history.log("audit APPROVED — promoting candidate")
                strategy_name = (state.get("implementation_plan") or {}).get(
                    "strategy_name", "Strategy",
                )
                target_dir = ARTIFACTS_DIR / "strategies" / f"v{iteration}"
                try:
                    promoted_path = _promote(
                        candidate_path=candidate_path,
                        target_dir=target_dir,
                        strategy_name=strategy_name,
                    )
                except ReviseTerminate as e:
                    return _terminate(e.reason, e.detail)
                history.final_verdict = "APPROVED"
                history.log(f"promoted to {promoted_path}")
                break  # break inner loop with success

            # audit REJECTED branch
            if audit_report.should_route_to_stage_c():
                history.log("→ CHECKLIST_AMBIGUOUS: requesting new checklist")
                checklist_feedback = (
                    f"audit 標記 CHECKLIST_AMBIGUOUS — reject_summary="
                    f"{audit_report.reject_summary}"
                )
                need_new_checklist = True
                break
            # IMPLEMENTATION_FAILED route from audit
            history.log(
                f"audit REJECTED (IMPLEMENTATION_FAILED): {audit_report.reject_summary}"
            )
            subagent_feedback = audit_report.reject_summary or "audit FAILED"
            counters["subagent_retry"] += 1
        else:
            # subagent_retry exhausted within this checklist
            return _terminate(
                "SUBAGENT_RETRY_EXHAUSTED",
                detail=f"3 subagent attempts under checklist_retry={ck_attempt} all failed",
            )

        if promoted_path is not None:
            break  # success path; exit checklist loop

        # If we got here, we want a new checklist
        if need_new_checklist:
            counters["checklist_retry"] += 1
            continue

        # Defensive: shouldn't reach here without a decision
        return _terminate("ORCHESTRATOR_BUG", detail="checklist loop fell through unexpectedly")
    else:
        return _terminate(
            "CHECKLIST_RETRY_EXHAUSTED",
            detail="3 checklist generations all failed (UNIMPLEMENTABLE / AMBIGUOUS / schema)",
        )

    # ------ Success path: write audit log, snapshot, update plan ------
    history.final_verdict = "APPROVED"
    _write_audit_log_md(
        history=history, counters=counters, iteration=iteration,
        output_path=audit_log_path,
    )
    artifacts.append({"type": "audit", "path": str(audit_log_path)})

    # Strategy spec snapshot: structural data extracted deterministically from
    # the promoted .py; checklist + intent.md provide narrative + delta.
    plan = dict(state.get("implementation_plan") or {})
    snapshot_path = ARTIFACTS_DIR / f"v{iteration}_strategy_spec.md"
    try:
        prev_py = state.get("implementation_plan", {}).get("strategy_file")
        intent_text = intent_path.read_text(encoding="utf-8") if intent_path else None
        write_strategy_spec_snapshot(
            py_path=promoted_path,
            prev_py_path=prev_py,
            checklist=checklist,
            intent_md=intent_text,
            output_path=snapshot_path,
        )
        artifacts.append({"type": "strategy_spec", "path": str(snapshot_path)})
    except ValueError as e:
        # Snapshot caught a post-audit divergence (param item ≠ AST value).
        # This is intentionally fatal: TERMINATE rather than upload an
        # incoherent snapshot.
        return _terminate("SNAPSHOT_DIVERGENCE", detail=str(e))
    except Exception as e:
        # Other snapshot failures are non-fatal; log and continue.
        history.log(f"strategy_spec snapshot failed (non-fatal): {e}")

    plan["strategy_file"] = str(promoted_path)

    history.log(
        f"orchestrator END verdict=APPROVED iteration={iteration} "
        f"ck_attempt={counters['checklist_retry']} sa_attempt={counters['subagent_retry']}"
    )

    return {
        "implementation_plan": plan,
        "last_result": "REVISED",
        "last_reason": f"v2 revise APPROVED at ck_attempt={counters['checklist_retry']} sa_attempt={counters['subagent_retry']}",
        "needs_human_approval": False,
        "artifacts": artifacts,
    }
