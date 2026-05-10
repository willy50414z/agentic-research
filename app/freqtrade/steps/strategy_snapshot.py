"""Strategy spec snapshot writer.

Renders ``v{N}_strategy_spec.md`` from a strategy ``.py`` plus optional
checklist + intent context. Per the openspec capability
``per-iteration-strategy-snapshot``, all structural content (class name,
timeframe, parameters, entry/exit conditions) MUST be deterministically
extracted from the post-audit ``.py`` via :mod:`app.freqtrade.strategy_extractor`.
The LLM-supplied ``intent.md`` is embedded verbatim under a clearly-labelled
"修訂摘要 (LLM 補充說明)" section and never used to derive structural values.

Delta rendering is type-split per spec:
  - ``type: param`` items render ``from → to`` and additionally cross-check the
    extracted AST value against ``checklist.to`` — mismatch raises ``ValueError``
    (this is the safety net guarding against post-audit divergence).
  - ``type: logic`` items render function + expected_signals + forbidden_signals
    + rationale (no ``from``/``to`` available; text-only delta).

Used by:
  - :mod:`app.freqtrade.steps.plan` (baseline v0 snapshot, no checklist/intent)
  - :mod:`app.freqtrade.steps.revise.v2` (post-promote v{N} snapshot)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.freqtrade import strategy_extractor as _ex
from app.freqtrade.checklist import (
    Checklist,
    ChecklistItem,
    ItemType,
    LogicTarget,
    ParamKind,
    ParamTarget,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: structural extraction
# ---------------------------------------------------------------------------


@dataclass
class _StructuralData:
    class_name: str | None
    timeframe: str
    stoploss: float | str
    minimal_roi: dict | str
    hyperopt_params: dict[str, dict]
    entry_conditions: str
    exit_conditions: str


def _extract_structural(py_text: str) -> _StructuralData:
    return _StructuralData(
        class_name=_ex.extract_class_name(py_text),
        timeframe=_ex.extract_timeframe(py_text),
        stoploss=_ex.extract_stoploss(py_text),
        minimal_roi=_ex.extract_minimal_roi(py_text),
        hyperopt_params=_ex.extract_hyperopt_params(py_text),
        entry_conditions=_ex.extract_entry_conditions(py_text),
        exit_conditions=_ex.extract_exit_conditions(py_text),
    )


# ---------------------------------------------------------------------------
# Internal: AST cross-check for param delta items
# ---------------------------------------------------------------------------


def _ast_value_for_param_item(item: ChecklistItem, structural: _StructuralData) -> object:
    """Look up the post-audit AST value for a ``type: param`` checklist item.

    Returns the extracted value (any literal type) or :data:`_ex.UNPARSEABLE`
    when the field could not be deterministically parsed.
    """
    if item.type != ItemType.PARAM or not isinstance(item.target, ParamTarget):
        raise TypeError(f"_ast_value_for_param_item: item {item.id} not type=param")
    target = item.target
    if target.kind == ParamKind.CLASS_ATTR:
        if target.name == "stoploss":
            return structural.stoploss
        if target.name == "timeframe":
            return structural.timeframe
        # Fallback for arbitrary class attributes: re-parse the .py is overkill,
        # so we just signal unparseable. The orchestrator's deterministic
        # check_param already verified equality during audit; this snapshot
        # path is informational.
        return _ex.UNPARSEABLE
    if target.kind == ParamKind.HYPEROPT_PARAM:
        params = structural.hyperopt_params or {}
        info = params.get(target.name) or {}
        return info.get(target.field or "default", _ex.UNPARSEABLE)
    if target.kind == ParamKind.DICT_VALUE:
        # Only minimal_roi is used today; resolve the path within it.
        head, *rest = (target.path or "").split(".", 1)
        if head != "minimal_roi" or not isinstance(structural.minimal_roi, dict):
            return _ex.UNPARSEABLE
        if not rest:
            return structural.minimal_roi
        # rest is e.g. '"0"' — strip surrounding quotes
        key = rest[0].strip().strip("'\"")
        return structural.minimal_roi.get(key, _ex.UNPARSEABLE)
    return _ex.UNPARSEABLE


# ---------------------------------------------------------------------------
# Internal: markdown rendering
# ---------------------------------------------------------------------------


def _format_value(v: object) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return repr(v)


def _render_structural(s: _StructuralData) -> str:
    lines: list[str] = ["## 結構性資料（從 `.py` AST 萃取）\n\n"]
    lines.append(f"- 類別: `{s.class_name or _ex.UNPARSEABLE}`\n")
    lines.append(f"- timeframe: `{s.timeframe}`\n")
    lines.append(f"- stoploss: `{_format_value(s.stoploss)}`\n")
    lines.append(f"- minimal_roi: `{_format_value(s.minimal_roi)}`\n")

    lines.append("\n### Hyperopt parameters\n\n")
    if not s.hyperopt_params:
        lines.append("（無）\n")
    else:
        for name in sorted(s.hyperopt_params):
            info = s.hyperopt_params[name]
            kind = info.get("kind", _ex.UNPARSEABLE)
            fields = ", ".join(
                f"{k}={_format_value(info.get(k, _ex.UNPARSEABLE))}"
                for k in ("low", "high", "default", "decimals")
                if k in info
            )
            lines.append(f"- `{name}`: {kind}({fields})\n")

    lines.append("\n### 進場條件 (populate_entry_trend)\n\n")
    lines.append(f"```\n{s.entry_conditions}\n```\n")

    lines.append("\n### 出場條件 (populate_exit_trend)\n\n")
    lines.append(f"```\n{s.exit_conditions}\n```\n")
    return "".join(lines)


def _render_delta_param_item(item: ChecklistItem, structural: _StructuralData) -> str:
    actual = _ast_value_for_param_item(item, structural)
    expected = item.to_value
    target = item.target
    target_desc = f"{target.kind.value}:{target.name}"  # type: ignore[union-attr]
    if isinstance(target, ParamTarget) and target.field:
        target_desc += f".{target.field}"
    if isinstance(target, ParamTarget) and target.path:
        target_desc += f" path={target.path}"

    # SoT cross-check: if extractor returned a parseable value and it doesn't
    # match the checklist's `to`, raise — this is the post-audit safety net.
    if actual != _ex.UNPARSEABLE and actual != expected:
        raise ValueError(
            f"strategy_spec snapshot: param item {item.id} expects {expected!r} "
            f"but extracted AST value is {actual!r} from {target_desc}"
        )
    ast_note = (
        f"AST 驗證: `{_format_value(actual)}` (匹配)" if actual == expected
        else f"AST 驗證: `{_ex.UNPARSEABLE}` (extractor 無法解析)"
    )

    lines = [
        f"### {item.id} (source: checklist.param)\n",
        f"- target: `{target_desc}`\n",
        f"- {_format_value(item.from_value)} → {_format_value(item.to_value)}\n",
        f"- {ast_note}\n",
    ]
    return "".join(lines)


def _render_delta_logic_item(item: ChecklistItem) -> str:
    target = item.target
    fn = target.function if isinstance(target, LogicTarget) else "?"
    lines = [
        f"### {item.id} (source: checklist.logic + AST)\n",
        f"- function: `{fn}`\n",
        "- 新增/修改行為:\n",
    ]
    for sig in item.expected_signals:
        lines.append(f"  - {sig}\n")
    if item.forbidden_signals:
        lines.append("- 明確移除的行為:\n")
        for sig in item.forbidden_signals:
            lines.append(f"  - {sig}\n")
    if item.rationale:
        lines.append(f"- 修訂理由: {item.rationale}\n")
    return "".join(lines)


def _render_delta(checklist: Checklist, structural: _StructuralData) -> str:
    lines = ["## Delta vs 前輪（來源: checklist）\n\n"]
    for item in checklist.items:
        if item.type == ItemType.PARAM:
            lines.append(_render_delta_param_item(item, structural))
        else:
            lines.append(_render_delta_logic_item(item))
        lines.append("\n")
    return "".join(lines)


def _render_intent_section(intent_md: str) -> str:
    return (
        "## 修訂摘要（LLM 補充說明）\n\n"
        "_本段為 LLM1 產出的修訂方向，**僅供敘事**；上方結構性資料才是 source of truth。_\n\n"
        f"{intent_md.rstrip()}\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_strategy_spec_snapshot(
    *,
    py_path: Path | str,
    prev_py_path: Path | str | None,
    checklist: Checklist | None,
    intent_md: str | None,
    output_path: Path | str,
) -> Path:
    """Render and write ``v{N}_strategy_spec.md``.

    Args:
        py_path: the audit-promoted ``.py`` (baseline for v0; the new
            promoted file for v{N>0}).
        prev_py_path: the previous iteration's ``.py``; reserved for future
            structural-diff display, currently unused (delta comes from
            checklist per spec).
        checklist: the checklist that drove the promotion. ``None`` for
            baseline v0 (no delta section is rendered).
        intent_md: the approved ``revision_intent.md`` text. ``None`` for
            baseline v0 (no LLM commentary section is rendered).
        output_path: destination markdown path.

    Returns:
        Path to the written markdown file.

    Raises:
        ValueError: if a ``type: param`` checklist item disagrees with the
            extracted AST value of the corresponding field — this signals a
            post-audit divergence and is intentionally fatal so callers can
            surface it as a TERMINATE rather than silently uploading an
            incoherent snapshot.
    """
    py = Path(py_path)
    out = Path(output_path)
    py_text = py.read_text(encoding="utf-8")
    structural = _extract_structural(py_text)

    parts: list[str] = []
    title_name = structural.class_name or py.stem
    parts.append(f"# Strategy Spec — {title_name}\n\n")
    parts.append(f"_來源: `{py}`_\n\n")

    parts.append(_render_structural(structural))
    parts.append("\n")

    if checklist is not None:
        parts.append(_render_delta(checklist, structural))

    if intent_md is not None:
        parts.append(_render_intent_section(intent_md))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return out
