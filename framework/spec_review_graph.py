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
    round == total - 1:     synthesizer → participants[0],   role="synthesize"

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

from framework.spec_clarifier import run_spec_agent, parse_spec_md
from framework.llm_providers import LLMProviderFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class SpecReviewState(TypedDict):
    project_id: str
    card_id: str
    spec_path: str
    participants: list          # ordered provider names from LLM_CHAIN
    current_round: int          # 0-indexed
    total_rounds: int           # len(participants) + 1
    current_spec_md: str        # updated by author/synthesizer; reviewer leaves unchanged
    review_notes: list          # list of dicts: {participant, round, questions}
    status: str                 # "in_progress" | "pass" | "need_update" | "abort"
    questions: list             # final questions for user (from synthesizer or abort)


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def _spec_review_init(state: dict, config: dict) -> dict:
    """
    Initialise the spec review state.
    Reads spec file content; sets participants, total_rounds, current_round=0.
    """
    llm_chain_str = (os.getenv("LLM_CHAIN") or "").strip()
    participants = [p.strip() for p in llm_chain_str.split(",") if p.strip()]
    if not participants:
        logger.error("spec_review_init: LLM_CHAIN is empty — aborting.")
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
        return {
            "participants": participants,
            "total_rounds": len(participants) + 1,
            "current_round": 0,
            "current_spec_md": "",
            "review_notes": [],
            "status": "abort",
            "questions": [f"Cannot read spec file: {e}"],
        }

    logger.info(
        "spec_review_init: project='%s' participants=%s total_rounds=%d",
        state.get("project_id"), participants, len(participants) + 1,
    )
    return {
        "participants": participants,
        "total_rounds": len(participants) + 1,
        "current_round": 0,
        "current_spec_md": current_spec_md,
        "review_notes": [],
        "status": "in_progress",
        "questions": [],
    }


def _spec_review_round(state: dict, config: dict) -> dict:
    """
    Execute one review round. Role and LLM are determined by current_round.

    Author (round 0):       participants[0],      role="initial"
    Reviewer (mid rounds):  participants[round],  role="review"
    Synthesizer (last):     participants[0],      role="synthesize"
    """
    participants = state.get("participants", [])
    current_round = state.get("current_round", 0)
    total_rounds = state.get("total_rounds", 1)
    spec_path = state.get("spec_path", "")
    work_dir = str(Path(spec_path).parent)

    if not participants:
        return {"status": "abort", "questions": ["No participants — cannot run review round."]}

    # Determine role and provider
    if current_round == 0:
        role = "initial"
        provider_name = participants[0]
    elif current_round < total_rounds - 1:
        role = "review"
        provider_name = participants[current_round]
    else:
        role = "synthesize"
        provider_name = participants[0]

    logger.info(
        "spec_review_round: project='%s' round=%d/%d role=%s provider=%s",
        state.get("project_id"), current_round + 1, total_rounds, role, provider_name,
    )

    # Build LLM callable
    llm_fn = LLMProviderFactory.build(provider_name)
    if llm_fn is None:
        raise RuntimeError(
            f"spec_review_round: cannot build provider '{provider_name}' — "
            "check LLM_CHAIN and ensure the provider is available."
        )

    # For review/synthesize roles, the spec_path is the current working spec
    # We write the current spec content to a temp file in work_dir so run_spec_agent reads it
    if role in ("review", "synthesize"):
        current_spec_path = Path(work_dir) / "current_spec_for_review.md"
        current_spec_path.write_text(state.get("current_spec_md", ""), encoding="utf-8")
        effective_spec_path = str(current_spec_path)
    else:
        effective_spec_path = spec_path

    result = run_spec_agent(
        spec_path=effective_spec_path,
        llm_fn=llm_fn,
        role=role,
        provider_name=provider_name,
        round_index=current_round,
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
        else:
            updates["status"] = "in_progress"
            logger.info("spec_review_round: %s role completed (pass).", role)

    return updates


def _spec_finalize(state: dict, config: dict) -> dict:
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
            sink.move_card(project_id, "Planning")
        return {}

    try:
        existing = get_project(project_id, db_url)
        plugin_name = (existing or {}).get("plugin_name") or parsed.get("plugin") or "quant_alpha"
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
            sink.move_card(project_id, "Planning")
        return {}

    _move("Verify")
    logger.info("spec_finalize: card '%s' moved to Verify.", project_id)

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
