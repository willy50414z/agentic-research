"""
framework/spec_review_graph.py

LangGraph StateGraph for the spec review workflow.

Replaces the plain-Python _run_spec_review_bg background task in server.py.
Uses PostgresSaver for checkpointing — each round is persisted, enabling
error resume without repeating completed LLM calls.

Flow:
    START → spec_review_init → spec_review_round (loop) → spec_finalize → END

Round roles (determined by current_round):
    round == 0:              author     → participants[0],   role="initial"
    0 < round < total - 1:  reviewer   → participants[round], role="review"
    round == total - 1:     synthesizer → participants[-1],  role="synthesize"

total_rounds = len(participants) + 1

Usage:
    from framework.spec_review_graph import get_or_build_spec_review_graph

    graph = get_or_build_spec_review_graph(config)
    graph.invoke(initial_state, config={"configurable": {"thread_id": project_id}})

Config keys:
    db_url            (str)       — psycopg3 connection string
    planka_sink       (optional)  — PlankaSink instance
    launch_research_fn (optional) — callable(project_id, initial_state) to start research graph
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TypedDict, Optional, Callable

import psycopg
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.postgres import PostgresSaver

from langchain_core.runnables import RunnableConfig

from framework.spec_clarifier import run_spec_agent, parse_spec_md
from framework.llm_providers import LLMProviderFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema (狀態定義)
# ---------------------------------------------------------------------------

class SpecReviewState(TypedDict):
    """
    規格審查圖表的狀態定義。
    """
    project_id: str             # 專案 ID
    card_id: str                # Planka 卡片 ID
    spec_path: str              # 原始規格書檔案路徑
    participants: list          # 參與審查的 LLM 供應商列表 (來自 LLM_CHAIN 變數)
    current_round: int          # 目前審查輪次 (從 0 開始)
    total_rounds: int           # 總輪次數（固定為 2：initial/refine + synthesize）
    current_spec_md: str        # 目前最新版的規格書 Markdown 內容
    review_notes: list          # 審查過程中記錄的意見與提問
    status: str                 # 狀態: in_progress | pass | need_update | abort
    questions: list             # 最終需要用戶回答的問題清單
    planka_comments: list       # 從 Planka 抓取的討論串內容
    has_pending_qa: bool        # 是否正在等待用戶回覆問題


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_spec_comment(sink, project_id: str, text: str) -> None:
    """Post a Planka comment for spec review progress. Non-blocking."""
    if sink is None:
        return
    try:
        sink.post_comment(project_id, text)
    except Exception as e:
        logger.warning("spec_review Planka comment failed (project='%s'): %s", project_id, e)


# ---------------------------------------------------------------------------
# Node implementations (節點實作)
# ---------------------------------------------------------------------------

def _spec_review_init(state: dict, config: RunnableConfig) -> dict:
    """
    Initialise the spec review state.
    Reads spec file content; sets participants, total_rounds, current_round=0.
    """
    project_id = state.get("project_id", "")
    cfg = config.get("configurable", {})
    sink = cfg.get("planka_sink")

    llm_chain_str = (os.getenv("LLM_CHAIN") or "").strip()
    participants = [p.strip() for p in llm_chain_str.split(",") if p.strip()]
    if not participants:
        logger.error("spec_review_init: LLM_CHAIN is empty — aborting.")
        _post_spec_comment(sink, project_id, "[SPEC-REVIEW] ABORT\nLLM_CHAIN env var is empty. Cannot run spec review.")
        return {
            "participants": [],
            "total_rounds": 0,
            "current_round": 0,
            "current_spec_md": "",
            "review_notes": [],
            "status": "abort",
            "questions": ["LLM_CHAIN env var is empty. Cannot run spec review."],
        }

    spec_path = state.get("spec_path", "")
    try:
        current_spec_md = Path(spec_path).read_text(encoding="utf-8")
    except Exception as e:
        logger.error("spec_review_init: cannot read spec file '%s': %s", spec_path, e)
        _post_spec_comment(sink, project_id, f"[SPEC-REVIEW] ABORT\nCannot read spec file: {e}")
        return {
            "participants": participants,
            "total_rounds": 2,
            "current_round": 0,
            "current_spec_md": "",
            "review_notes": [],
            "status": "abort",
            "questions": [f"Cannot read spec file: {e}"],
        }

    # Detect prior Q&A: look for a system question comment followed by at least one user reply.
    _QUESTION_MARKER = "**Spec 審查問題**"
    comments = state.get("planka_comments") or []
    question_indices = [
        i for i, c in enumerate(comments)
        if _QUESTION_MARKER in c.get("text", "")
    ]
    if question_indices and question_indices[-1] < len(comments) - 1:
        # There is a question comment AND at least one comment after it (user reply).
        has_pending_qa = True
        total_rounds = 2  # refine (llm-1) + synthesize (llm-2)
    else:
        has_pending_qa = False
        total_rounds = 2  # initial (llm-1) + synthesize (llm-2)

    mode = "refine (Q&A detected)" if has_pending_qa else "initial"
    logger.info(
        "spec_review_init: project='%s' participants=%s total_rounds=%d has_pending_qa=%s",
        project_id, participants, total_rounds, has_pending_qa,
    )
    _post_spec_comment(
        sink, project_id,
        f"[SPEC-REVIEW] START\n"
        f"mode: {mode}\n"
        f"providers: {', '.join(participants)}\n"
        f"total_rounds: {total_rounds}",
    )
    return {
        "participants": participants,
        "total_rounds": total_rounds,
        "current_round": 0,
        "current_spec_md": current_spec_md,
        "review_notes": [],
        "status": "in_progress",
        "questions": [],
        "has_pending_qa": has_pending_qa,
    }


def _format_qa_history(comments: list) -> str:
    """
    Extract the last spec-review Q&A exchange from the Planka comment thread.

    Returns only:
      - The last comment containing _QUESTION_MARKER (the system's questions)
      - All comments after it (the user's replies)

    This avoids passing unrelated earlier discussion to the LLM.
    """
    _QUESTION_MARKER = "**Spec 審查問題**"
    last_q_index = None
    for i, c in enumerate(comments):
        if _QUESTION_MARKER in c.get("text", ""):
            last_q_index = i

    if last_q_index is None:
        return "(no spec review questions found)"

    relevant = comments[last_q_index:]
    parts = []
    for c in relevant:
        ts   = c.get("createdAt", "")
        text = c.get("text", "").strip()
        parts.append(f"=== {ts} ===\n{text}")
    return "\n\n".join(parts)


def _spec_review_round(state: dict, config: RunnableConfig) -> dict:
    """
    Execute one review round. Role and LLM are determined by current_round.

    Refine (has_pending_qa):  participants[0],   role="refine"
    Author (round 0):         participants[0],   role="initial"
    Synthesizer (last):       participants[-1],  role="synthesize"
    """
    participants = state.get("participants", [])
    current_round = state.get("current_round", 0)
    total_rounds = state.get("total_rounds", 1)
    spec_path = state.get("spec_path", "")
    work_dir = str(Path(spec_path).parent)

    if not participants:
        return {"status": "abort", "questions": ["No participants — cannot run review round."]}

    # Determine role and provider
    if state.get("has_pending_qa") and current_round == 0:
        role = "refine"
        provider_name = participants[0]
    elif current_round == 0:
        role = "initial"
        provider_name = participants[0]
    else:
        role = "synthesize"
        provider_name = participants[-1]

    project_id = state.get("project_id", "")
    cfg = config.get("configurable", {})
    sink = cfg.get("planka_sink")

    logger.info(
        "spec_review_round: project='%s' round=%d/%d role=%s provider=%s",
        project_id, current_round + 1, total_rounds, role, provider_name,
    )
    _post_spec_comment(
        sink, project_id,
        f"[SPEC-REVIEW] ROUND {current_round + 1}/{total_rounds}\n"
        f"role: {role}\n"
        f"provider: {provider_name}",
    )

    # Build LLM callable
    llm_fn = LLMProviderFactory.build(provider_name)
    if llm_fn is None:
        raise RuntimeError(
            f"spec_review_round: cannot build provider '{provider_name}' — "
            "check LLM_CHAIN and ensure the provider is available."
        )

    # Determine which spec file to pass to the agent.
    # refine: read the already-reviewed initial draft, not the raw spec
    # synthesize: read the spec produced by the author/refine round (stored in state)
    if role == "refine":
        reviewed_final   = Path(work_dir) / "reviewed_spec_final.md"
        reviewed_initial = Path(work_dir) / "reviewed_spec_initial.md"
        if reviewed_final.exists():
            # Second or later refine: build on the previous refine/synthesize output.
            effective_spec_path = str(reviewed_final)
            logger.info("spec_review_round: refine using reviewed_spec_final.md as base.")
        elif reviewed_initial.exists():
            # First refine after initial round (edge-case: synthesize did not run yet).
            effective_spec_path = str(reviewed_initial)
            logger.warning(
                "spec_review_round: reviewed_spec_final.md not found, "
                "falling back to reviewed_spec_initial.md."
            )
        else:
            logger.error(
                "spec_review_round: refine path requires reviewed_spec_final.md or "
                "reviewed_spec_initial.md but neither found in '%s' — aborting.",
                work_dir,
            )
            return {
                "status": "abort",
                "questions": [
                    f"Refine 路徑找不到上一輪審查產出（reviewed_spec_final.md 或 reviewed_spec_initial.md）。"
                    "請確認首次審查是否已正常完成，或重新將卡片移回 Spec Pending Review 觸發完整審查。"
                ],
            }
    elif role == "synthesize":
        current_spec_path = Path(work_dir) / "current_spec_for_review.md"
        current_spec_path.write_text(state.get("current_spec_md", ""), encoding="utf-8")
        effective_spec_path = str(current_spec_path)
    else:
        effective_spec_path = spec_path

    comment_history = (
        _format_qa_history(state.get("planka_comments") or [])
        if role == "refine"
        else ""
    )

    result = run_spec_agent(
        spec_path=effective_spec_path,
        llm_fn=llm_fn,
        role=role,
        provider_name=provider_name,
        round_index=current_round,
        comment_history=comment_history,
    )

    # Build state updates
    updates: dict = {"current_round": current_round + 1}

    if role == "review":
        # Reviewer: append notes, do not change spec
        new_note = {
            "participant": provider_name,
            "round": current_round,
            "questions": result.questions,
        }
        updates["review_notes"] = list(state.get("review_notes", [])) + [new_note]
        logger.info(
            "spec_review_round: reviewer '%s' raised %d notes.",
            provider_name, len(result.questions),
        )
        _post_spec_comment(
            sink, project_id,
            f"[SPEC-REVIEW] ROUND {current_round + 1} DONE\n"
            f"role: {role}  provider: {provider_name}\n"
            f"notes raised: {len(result.questions)}",
        )
    else:
        # Author / synthesizer: update the spec
        updates["current_spec_md"] = result.enhanced_spec_md
        if result.needs_user_input:
            updates["status"] = "need_update"
            updates["questions"] = result.questions
            logger.info(
                "spec_review_round: %s role resulted in need_update (%d questions).",
                role, len(result.questions),
            )
            _post_spec_comment(
                sink, project_id,
                f"[SPEC-REVIEW] ROUND {current_round + 1} DONE\n"
                f"role: {role}  provider: {provider_name}\n"
                f"status: need_update  questions: {len(result.questions)}",
            )
        else:
            updates["status"] = "in_progress"
            logger.info("spec_review_round: %s role completed (pass).", role)
            _post_spec_comment(
                sink, project_id,
                f"[SPEC-REVIEW] ROUND {current_round + 1} DONE\n"
                f"role: {role}  provider: {provider_name}\n"
                f"status: pass",
            )

    return updates


def _spec_finalize(state: dict, config: RunnableConfig) -> dict:
    """
    Finalize the spec review.

    - status == "pass" or author/synthesizer completed without questions:
        parse spec, upsert project, move card to Verify, optionally launch research graph
    - status == "need_update":
        post questions as Planka comment, move card to Planning
    - status == "abort":
        post error as Planka comment, move card to Planning
    """
    from framework.db.queries import create_project, get_project

    project_id = state.get("project_id", "")
    card_id    = state.get("card_id", "")
    status     = state.get("status", "abort")
    questions  = state.get("questions", [])
    spec_md    = state.get("current_spec_md", "")

    cfg = config.get("configurable", {})
    sink             = cfg.get("planka_sink")
    db_url           = cfg.get("db_url") or os.getenv("DATABASE_URL", "")
    move_card_fn     = cfg.get("move_card_fn")    # callable(project_id, column_name)
    launch_research  = cfg.get("launch_research_fn")  # optional callback

    def _move(column: str) -> None:
        if move_card_fn:
            move_card_fn(project_id, column)

    if status == "need_update":
        logger.info("spec_finalize: need_update — posting questions for project '%s'.", project_id)
        if sink:
            q_text = "\n".join(f"- {q}" for q in questions)
            sink.post_comment(project_id, f"**Spec 審查問題**\n\n{q_text}")
        _move("Planning")
        return {}

    if status == "abort":
        reason = questions[0] if questions else "Unknown error during spec review."
        logger.error("spec_finalize: abort for project '%s': %s", project_id, reason)
        if sink:
            sink.post_comment(project_id, f"**Spec Review 失敗**\n\n{reason}")
        _move("Planning")
        return {}

    # status == "in_progress" after synthesizer completed successfully (pass)
    logger.info("spec_finalize: pass — finalising project '%s'.", project_id)
    try:
        parsed = parse_spec_md(spec_md)
    except Exception as e:
        logger.exception("spec_finalize: parse_spec_md failed: %s", e)
        if sink:
            sink.post_comment(project_id, f"**Spec 解析失敗**\n\n{e}")
        _move("Planning")
        return {}

    try:
        existing = get_project(project_id, db_url)
        existing_plugin = (existing or {}).get("plugin_name") or ""
        if existing_plugin == "unknown":
            existing_plugin = ""
        plugin_name = existing_plugin or parsed.get("plugin") or "quant_alpha"
        hypothesis  = parsed.get("hypothesis") or state.get("project_id")

        create_project(
            project_id=project_id,
            name=existing.get("name", project_id) if existing else project_id,
            plugin_name=plugin_name,
            goal=hypothesis,
            config={
                "spec": parsed,
                "review_in_progress": False,
            },
            db_url=db_url,
        )
        logger.info("spec_finalize: project '%s' upserted with plugin='%s'.", project_id, plugin_name)
    except Exception as e:
        logger.exception("spec_finalize: create_project failed: %s", e)
        if sink:
            sink.post_comment(project_id, f"**Project 建立失敗**\n\n{e}")
        _move("Planning")
        return {}

    _move("Verify")
    logger.info("spec_finalize: card '%s' moved to Verify.", project_id)
    if sink:
        sink.post_comment(
            project_id,
            f"[SPEC-REVIEW] PASS\n"
            f"plugin: {plugin_name}\n"
            f"hypothesis: {str(hypothesis)[:200]}\n"
            f"Card moved to Verify.",
        )

    if launch_research is not None:
        try:
            launch_research(project_id, parsed)
            logger.info("spec_finalize: research graph launched for '%s'.", project_id)
        except Exception as e:
            logger.warning("spec_finalize: launch_research_fn failed (non-blocking): %s", e)

    return {}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_review(state: dict) -> str:
    """Continue looping until the last round is done, then finalize."""
    if state.get("status") in ("abort", "need_update"):
        return "spec_finalize"
    current = state.get("current_round", 0)
    total   = state.get("total_rounds", 1)
    if current < total:
        return "spec_review_round"
    return "spec_finalize"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_spec_review_graph(config: dict):
    """
    Build and compile the spec review StateGraph.

    Args:
        config: dict with keys:
            db_url  (str)  — psycopg3 connection string
            (others passed through to nodes via configurable)
    """
    db_url = config.get("db_url") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("db_url must be provided in config or DATABASE_URL env var.")

    workflow = StateGraph(SpecReviewState)

    workflow.add_node("spec_review_init",  _spec_review_init)
    workflow.add_node("spec_review_round", _spec_review_round)
    workflow.add_node("spec_finalize",     _spec_finalize)

    workflow.add_edge(START, "spec_review_init")
    workflow.add_edge("spec_review_init", "spec_review_round")
    workflow.add_conditional_edges(
        "spec_review_round",
        _route_review,
        {
            "spec_review_round": "spec_review_round",
            "spec_finalize":     "spec_finalize",
        },
    )
    workflow.add_edge("spec_finalize", END)

    conn = psycopg.connect(db_url, autocommit=True)
    checkpointer = PostgresSaver(conn)
    try:
        checkpointer.setup()
    except Exception as e:
        if "already exists" in str(e).lower() or "unique" in str(e).lower():
            logger.debug("spec_review checkpointer.setup() skipped (tables exist): %s", e)
        else:
            raise

    compiled = workflow.compile(checkpointer=checkpointer)
    logger.info("SpecReviewGraph compiled.")
    return compiled


# ---------------------------------------------------------------------------
# Graph cache
# ---------------------------------------------------------------------------

_spec_review_graph_cache: dict[str, object] = {}


def get_or_build_spec_review_graph(config: dict):
    """Return a cached compiled spec review graph."""
    db_url = config.get("db_url") or os.getenv("DATABASE_URL", "")
    key = f"spec_review:{db_url}"
    if key not in _spec_review_graph_cache:
        _spec_review_graph_cache[key] = build_spec_review_graph(config)
    return _spec_review_graph_cache[key]
