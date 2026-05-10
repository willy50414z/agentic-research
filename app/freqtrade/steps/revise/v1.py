"""v1 revise_step — legacy 2-LLM JSON validation flow.

Kept as fallback while the v2 pipeline (intent → checklist → subagent → audit)
matures. Selected via ``REVISE_PIPELINE_VERSION`` env var; defaults to v1 for
safe rollback. Removal scheduled in tasks.md §10.8 after v2 production validation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .._common import (
    ARTIFACTS_DIR,
    _call_llm,
    _load_prompt,
    _read_json_file,
    _write_artifact,
)

logger = logging.getLogger(__name__)


def revise_step_v1(state: dict) -> dict:
    """Legacy 2-LLM JSON revision flow (pre-checklist-audit).

    LLM-1 drafts a revised plan dict, LLM-2 validates and finalises it.
    Falls back to a deterministic stoploss-tightening rule when either LLM call
    fails. Produces ``v{N}_revised_direction.md`` for traceability.
    """
    loop    = state.get("loop_index", 0)
    attempt = state.get("analyze_attempt", 0)
    plan    = dict(state.get("implementation_plan") or {})
    reason  = state.get("last_reason", "")
    logger.info("[workflow][revise[v1]] START  loop=%d  analyze_attempt=%d", loop, attempt)

    output_dir = str(ARTIFACTS_DIR.resolve())

    # --- LLM 1: draft revision ---
    draft: dict = {}
    draft_reason = reason
    prompt1 = _load_prompt("revise").format(
        params        = json.dumps(plan, ensure_ascii=False, indent=2),
        reason        = reason,
        attempt_count = attempt,
        OUTPUT_DIR    = output_dir,
    )
    try:
        _call_llm(prompt1, cwd=output_dir)
        result_file = Path(output_dir) / "revise_result.txt"
        lines  = result_file.read_text(encoding="utf-8").strip().splitlines()
        status = lines[0].strip().upper() if lines else "TERMINATE"
        draft_reason = lines[1].strip() if len(lines) > 1 else reason
        if status == "TERMINATE":
            return {"last_result": "TERMINATE", "last_reason": draft_reason}
        draft = _read_json_file(Path(output_dir) / "revised_params.json")
        (Path(output_dir) / "revise_draft.json").write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] revise[v1] LLM-1 unavailable (%s) — rule-based fallback", e)
    except Exception as e:
        logger.warning("[freqtrade] revise[v1] LLM-1 failed (%s) — rule-based fallback", e)

    if not draft:
        draft = dict(plan)
        draft["stoploss"] = round(max(-0.02, plan.get("stoploss", -0.05) + 0.01), 3)
        draft_reason = f"Tightened stoploss to {draft['stoploss']} (rule-based fallback)."

    # --- LLM 2: validate & supplement ---
    revised: dict = {}
    validate_reason = draft_reason
    prompt2 = _load_prompt("revise_validate").format(
        original_params = json.dumps(plan, ensure_ascii=False, indent=2),
        reason          = reason,
        draft_params    = json.dumps(draft, ensure_ascii=False, indent=2),
        OUTPUT_DIR      = output_dir,
    )
    try:
        _call_llm(prompt2, cwd=output_dir)
        validate_file = Path(output_dir) / "revise_validate_result.txt"
        vlines = validate_file.read_text(encoding="utf-8").strip().splitlines()
        validate_reason = vlines[1].strip() if len(vlines) > 1 else draft_reason
        revised = _read_json_file(Path(output_dir) / "revised_params.json")
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] revise[v1] LLM-2 unavailable (%s) — using LLM-1 draft", e)
    except Exception as e:
        logger.warning("[freqtrade] revise[v1] LLM-2 failed (%s) — using LLM-1 draft", e)

    if not revised:
        revised = draft
        validate_reason = draft_reason

    # --- Produce v{N}_revised_direction.md ---
    direction_lines = [
        f"# v{attempt} 修訂方向\n",
        f"**失敗原因**：{reason}\n",
        f"**LLM 1 草稿**：{draft_reason}\n",
        f"**LLM 2 驗證結論**：{validate_reason}\n",
        "## 參數對照\n",
        "| 參數 | 修訂前 | 修訂後 |\n|------|--------|--------|\n",
    ]
    for key in sorted(set(list(plan.keys()) + list(revised.keys()))):
        old_val = plan.get(key, "—")
        new_val = revised.get(key, "—")
        if old_val != new_val:
            direction_lines.append(f"| {key} | {old_val} | {new_val} |\n")
    direction_path = str(ARTIFACTS_DIR / f"v{attempt}_revised_direction.md")
    _write_artifact(direction_path, "".join(direction_lines))

    logger.info("[workflow][revise[v1]] END  loop=%d  analyze_attempt=%d", loop, attempt)
    return {
        "implementation_plan":  revised,
        "last_reason":          validate_reason,
        "needs_human_approval": False,
        "artifacts": state.get("artifacts", []) + [
            {"type": "revised_direction", "path": direction_path},
        ],
    }
