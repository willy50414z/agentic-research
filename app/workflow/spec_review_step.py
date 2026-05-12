"""
app/spec_review.py

Spec review workflow using llm_eval.evaluate() — replaces spec_review_graph.py.

Flow (2 steps tracked by workflow_step):
  spec_review_initial    — first LLM pass: writes reviewed_spec_initial.md
                           or questions.txt if spec needs clarification
  spec_review_synthesize — final LLM pass: writes reviewed_spec_final.md
                           or questions.txt

After each step:
  pass        → advance to next step (or move card to Executing)
  need_update → post questions to Planka, move card to Planning
"""

import json
import logging
import shutil
import time
from pathlib import Path

from llm_eval import evaluate, Outcome, JobResult, LLMEvaluationError

from app.llm import get_llm_targets
from app.db.queries import (
    get_project, create_project, get_workflow_step, set_workflow_step, merge_config,
)
from app.workflow.error_report import write_error_report

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "spec_review"
_RULES_DIR = Path(__file__).parent.parent.parent / ".ai" / "rules"
_SAMPLE_SPEC_PATH = _PROMPTS_DIR / "sample_spec.md"

_QA_MARKER = "**Spec 審查問題**"
_REVIEW_TTL = 40 * 60  # seconds before a stale in-progress lock is cleared
_HYPOTHESIS_PREVIEW_MAX = 200
_UPLOAD_SKIP_FILES = frozenset({
    "status_pass.txt", "status_need_update.txt", "spec.md",
    "spec-review.md", "current_spec_for_review.md",
})

_STEP_INITIAL = "spec_review_initial"
_STEP_SYNTHESIZE = "spec_review_synthesize"
_STEP_PLAN = "plan"

_COL_PLANNING = "Planning"
_COL_EXECUTING = "Executing"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Could not read '%s': %s", path, e)
        return ""


def _render_prompt(name: str, replacements: dict) -> str:
    template = _load_prompt(name)
    for key, value in replacements.items():
        template = template.replace(f"{{{key}}}", value)
    return template


# ---------------------------------------------------------------------------
# Public entry point (called from server.py webhook handler)
# ---------------------------------------------------------------------------

def run_spec_review_step(
    project_id: str,
    card_id: str,
    card_name: str,
    description: str,
    db_url: str | None = None,
    planka_client=None,
    move_card_fn=None,
) -> None:
    SpecReviewRunner(
        project_id=project_id,
        card_id=card_id,
        card_name=card_name,
        description=description,
        db_url=db_url,
        planka_client=planka_client,
        move_card_fn=move_card_fn,
    ).run()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SpecReviewRunner:
    def __init__(
        self,
        project_id: str,
        card_id: str,
        card_name: str,
        description: str,
        db_url: str | None,
        planka_client,
        move_card_fn,
    ):
        self._project_id = project_id
        self._card_id = card_id
        self._card_name = card_name
        self._description = description
        self._db_url = db_url
        self._planka_client = planka_client
        self._move_card_fn = move_card_fn

    def run(self) -> None:
        now = time.time()
        step = get_workflow_step(self._project_id, self._db_url)
        logger.info("[spec-review] project='%s' step='%s'", self._project_id, step)

        existing = get_project(self._project_id, self._db_url)
        if existing:
            config = existing.get("config") or {}
            if config.get("review_in_progress"):
                age = now - (config.get("review_started_at") or 0)
                if age < _REVIEW_TTL:
                    logger.warning("[spec-review] SKIP — review already in progress (%.0fs)", age)
                    return
                logger.warning("[spec-review] stale review_in_progress (%.0fs), clearing.", age)

        if self._planka_client:
            self._planka_client.cache_card_id(self._project_id, self._card_id)
        if existing:
            merge_config(
                self._project_id,
                {"review_in_progress": True, "review_started_at": now},
                self._db_url,
            )
        else:
            create_project(
                project_id=self._project_id, name=self._card_name, plugin_name="unknown", goal="",
                config={"review_in_progress": True, "review_started_at": now},
                db_url=self._db_url,
            )

        if self._planka_client and "thread_id:" not in (self._description or ""):
            self._planka_client.update_card_description(
                self._project_id,
                f"thread_id: {self._project_id}\n\n{self._description or ''}",
            )

        spec_path = self._planka_client.download_latest_spec_attachment(self._card_id) if self._planka_client else None
        if not spec_path:
            logger.warning("[spec-review] ABORT — no spec.md for card '%s'", self._card_name)
            if self._planka_client:
                self._planka_client.post_comment(
                    self._project_id,
                    "**Missing spec.md**\n\n"
                    "請先將 `spec.md` 上傳為卡片附件，再移至 Spec Pending Review。",
                )
            self._clear_flag()
            self._do_move(_COL_PLANNING)
            return

        work_dir = Path(spec_path).parent

        try:
            if step in (None, _STEP_INITIAL):
                self._run_initial(Path(spec_path), work_dir)
            elif step == _STEP_SYNTHESIZE:
                self._run_synthesize(Path(spec_path), work_dir)
            else:
                logger.warning("[spec-review] unexpected step='%s' — re-running initial", step)
                self._run_initial(Path(spec_path), work_dir)
        except LLMEvaluationError as e:
            logger.exception("[spec-review] LLM ERROR project='%s': %s", self._project_id, e)
            self._clear_flag()
            if self._planka_client:
                self._planka_client.post_comment(
                    self._project_id,
                    f"**LLM 執行失敗**\n\n```\n{e}\n```",
                )
            write_error_report(self._project_id, e, "spec_review", self._db_url)
            self._do_move(_COL_PLANNING)
            return
        except Exception as e:
            logger.exception("[spec-review] ERROR project='%s': %s", self._project_id, e)
            self._clear_flag()
            if self._planka_client:
                self._planka_client.post_comment(
                    self._project_id,
                    f"**Spec review 處理失敗**\n\n```\n{type(e).__name__}: {e}\n```",
                )
            write_error_report(self._project_id, e, "spec_review", self._db_url)
            self._do_move(_COL_PLANNING)
            return

        self._clear_flag()

        if self._planka_client:
            self._upload_work_dir(work_dir)

    # ---------------------------------------------------------------------------
    # Round runners
    # ---------------------------------------------------------------------------

    def _run_initial(self, spec_path: Path, work_dir: Path) -> None:
        """step=spec_review_initial: initial review, or refine if Q&A history exists."""
        planka_comments = self._planka_client.get_card_comments(self._card_id) if self._planka_client else []
        has_qa = _detect_qa(planka_comments)
        prompt_file = "spec_agent_refine" if has_qa else "spec_agent_initial"

        qa_history = _format_qa_history(planka_comments) if has_qa else ""
        spec_text = spec_path.read_text(encoding="utf-8")
        constraints = _load_text(_RULES_DIR / "spec-review-agent-constraints.md")
        rules = _load_text(_RULES_DIR / "spec-review.md")
        sample_spec = _load_text(_SAMPLE_SPEC_PATH)

        def purpose(ws: Path) -> str:
            return _render_prompt(prompt_file, {
                "SPEC": spec_text,
                "CONSTRAINTS": constraints,
                "RULES": rules,
                "SAMPLE_SPEC": sample_spec,
                "COMMENT_HISTORY": qa_history,
                "OUTPUT_DIR": str(ws),
            })

        proceed = {"synthesize": False}

        def on_pass(job: JobResult) -> None:
            if "reviewed_spec_initial.md" in job.files:
                (work_dir / "reviewed_spec_initial.md").write_text(
                    job.files["reviewed_spec_initial.md"].decode("utf-8"), encoding="utf-8"
                )
            logger.info("[spec-review] initial round PASS  job=%s", job.job_id)
            set_workflow_step(self._project_id, _STEP_SYNTHESIZE, self._db_url)
            if self._planka_client:
                self._planka_client.post_comment(
                    self._project_id,
                    "[SPEC-REVIEW] initial round PASS — proceeding to synthesize",
                )
            proceed["synthesize"] = True

        def on_need_update(job: JobResult) -> None:
            logger.info("[spec-review] initial round NEED_UPDATE  job=%s", job.job_id)
            raw = job.files.get("questions.txt", b"")
            self._handle_need_update(raw.decode("utf-8") if isinstance(raw, bytes) else raw, _STEP_INITIAL)

        def on_error(job: JobResult) -> None:
            logger.error("[spec-review] initial round: LLM produced no status file  job=%s", job.job_id)
            raise LLMEvaluationError("LLM did not produce a status file during initial review")

        if self._planka_client:
            label = "refine" if has_qa else "initial"
            self._planka_client.post_comment(
                self._project_id,
                f"[SPEC-REVIEW] 開始執行 {label} round — LLM 審查中，請稍候…",
            )

        evaluate(
            target=None,
            targets=get_llm_targets("spec_review_initial"),
            purpose=purpose,
            outcomes=[
                Outcome(status="pass", description="Spec complete after initial review",
                        output_files=["reviewed_spec_initial.md"], callback=on_pass),
                Outcome(status="need_update", description="Spec has gaps requiring user input",
                        output_files=["questions.txt"], callback=on_need_update),
                Outcome(status="error", description="LLM failed to signal outcome",
                        callback=on_error),
            ],
            cwd=str(work_dir),
        )

        if proceed["synthesize"]:
            self._run_synthesize(spec_path, work_dir)

    def _run_synthesize(self, spec_path: Path, work_dir: Path) -> None:
        """step=spec_review_synthesize: finalise the spec, or return to Planning if gaps remain."""
        reviewed_initial = work_dir / "reviewed_spec_initial.md"
        base_spec = reviewed_initial if reviewed_initial.exists() else spec_path
        spec_text = base_spec.read_text(encoding="utf-8")
        constraints = _load_text(_RULES_DIR / "spec-review-agent-constraints.md")
        rules = _load_text(_RULES_DIR / "spec-review.md")

        def purpose(ws: Path) -> str:
            return _render_prompt("spec_agent_synthesize", {
                "SPEC": spec_text,
                "CONSTRAINTS": constraints,
                "RULES": rules,
                "OUTPUT_DIR": str(ws),
            })

        def _decode(v: bytes | str) -> str:
            return v.decode("utf-8") if isinstance(v, bytes) else v

        def on_pass(job: JobResult) -> None:
            logger.info("[spec-review] synthesize PASS  job=%s", job.job_id)
            final_md = _decode(job.files.get("reviewed_spec_final.md", b""))
            if final_md:
                (work_dir / "reviewed_spec_final.md").write_text(final_md, encoding="utf-8")
            self._finalise_pass(
                final_md,
                _decode(job.files.get("spec_fields.json", b"")),
            )

        def on_need_update(job: JobResult) -> None:
            logger.info("[spec-review] synthesize NEED_UPDATE  job=%s", job.job_id)
            self._handle_need_update(_decode(job.files.get("questions.txt", b"")), _STEP_INITIAL)

        def on_error(job: JobResult) -> None:
            logger.error("[spec-review] synthesize: LLM produced no status file  job=%s", job.job_id)
            raise LLMEvaluationError("LLM did not produce a status file during synthesize review")

        if self._planka_client:
            self._planka_client.post_comment(
                self._project_id,
                "[SPEC-REVIEW] 開始執行 synthesize round — LLM 最終審查中，請稍候…",
            )

        evaluate(
            target=None,
            targets=get_llm_targets("spec_review_synthesize"),
            purpose=purpose,
            outcomes=[
                Outcome(status="pass", description="Spec is complete and well-formed",
                        output_files=["reviewed_spec_final.md", "spec_fields.json"], callback=on_pass),
                Outcome(status="need_update", description="Spec still has unresolved gaps",
                        output_files=["questions.txt"], callback=on_need_update),
                Outcome(status="error", description="LLM failed to signal outcome",
                        callback=on_error),
            ],
            cwd=str(work_dir),
        )

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _handle_need_update(self, questions_text: str, reset_step: str) -> None:
        self._post_questions(questions_text)
        set_workflow_step(self._project_id, reset_step, self._db_url)
        self._do_move(_COL_PLANNING)

    def _finalise_pass(self, spec_md: str, spec_fields_json: str) -> None:
        try:
            fields = json.loads(spec_fields_json) if spec_fields_json else {}
        except json.JSONDecodeError:
            logger.warning("[spec-review] spec_fields.json parse failed — using defaults")
            fields = {}

        plugin_name = fields.get("plugin") or "quant_alpha"
        hypothesis = fields.get("hypothesis") or self._project_id

        create_project(
            project_id=self._project_id, name=self._project_id,
            plugin_name=plugin_name, goal=hypothesis,
            config={"spec": {**fields, "raw_md": spec_md}, "review_in_progress": False},
            db_url=self._db_url,
        )
        set_workflow_step(self._project_id, _STEP_PLAN, self._db_url)
        if self._planka_client:
            self._planka_client.post_comment(
                self._project_id,
                f"[SPEC-REVIEW] PASS\nplugin: {plugin_name}\nhypothesis: {str(hypothesis)[:_HYPOTHESIS_PREVIEW_MAX]}",
            )
        self._do_move(_COL_EXECUTING)

    def _post_questions(self, questions_text: str) -> None:
        if self._planka_client and questions_text:
            self._planka_client.post_comment(
                self._project_id,
                f"{_QA_MARKER}\n\n{questions_text.strip()}",
            )

    def _clear_flag(self) -> None:
        try:
            merge_config(self._project_id, {"review_in_progress": False}, self._db_url)
        except Exception as e:
            logger.warning("[spec-review] _clear_flag failed: %s", e)

    def _do_move(self, column: str) -> None:
        if self._move_card_fn:
            try:
                self._move_card_fn(self._project_id, column)
            except Exception as e:
                logger.warning("[spec-review] _do_move '%s' failed for project='%s': %s",
                               column, self._project_id, e)

    def _upload_work_dir(self, work_dir: Path) -> None:
        def _upload_priority(p: Path) -> tuple:
            if p.name == "reviewed_spec_initial.md":
                return (0, p.name)
            if p.name == "reviewed_spec_final.md":
                return (1, p.name)
            return (2, p.name)

        failed = False
        for fpath in sorted(work_dir.iterdir(), key=_upload_priority):
            if not fpath.is_file() or fpath.name in _UPLOAD_SKIP_FILES:
                continue
            try:
                self._planka_client.upload_spec_attachment(
                    self._card_id, fpath.name, fpath.read_text(encoding="utf-8"),
                )
            except Exception as e:
                logger.warning("[spec-review] upload '%s' failed: %s", fpath.name, e)
                failed = True
        if failed:
            logger.warning("[spec-review] skipping cleanup — some uploads failed, work_dir preserved: %s", work_dir)
            return
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            logger.warning("[spec-review] rmtree '%s' failed: %s", work_dir, e)


# ---------------------------------------------------------------------------
# Pure helpers (no shared state — remain module-level)
# ---------------------------------------------------------------------------


def _detect_qa(comments: list) -> bool:
    """Returns True if there is a QA marker that is not the last comment."""
    for i in range(len(comments) - 2, -1, -1):
        if _QA_MARKER in comments[i].get("text", ""):
            return True
    return False


def _format_qa_history(comments: list) -> str:
    last_q = next(
        (i for i in range(len(comments) - 1, -1, -1)
         if _QA_MARKER in comments[i].get("text", "")),
        None,
    )
    if last_q is None:
        return ""
    return "\n\n".join(
        f"=== {c.get('createdAt', '')} ===\n{c.get('text', '').strip()}"
        for c in comments[last_q:]
    )
