"""
app/freqtrade/strategy_extractor.py

Deterministic AST extraction of structural fields from a freqtrade strategy
``.py`` source. Used by the per-iteration ``v{N}_strategy_spec.md`` snapshot
to enforce the "SoT 紀律" rule: structural values (class name, timeframe,
stoploss, minimal_roi, hyperopt parameter fields, entry/exit conditions)
SHALL be produced by deterministic AST parsing only — never by an LLM.

When extraction fails for a given field, the corresponding function returns
the sentinel ``UNPARSEABLE`` ("<unparseable>") and logs a warning. Callers
MUST NOT attempt to recover via LLM inference (per
``per-iteration-strategy-snapshot`` capability spec).

Several private AST helpers in :mod:`app.freqtrade.audit` already implement
the same node walks we need (``_find_strategy_class``, ``_find_class_attr``,
``_literal_value``, ``_find_hyperopt_call``, ``_hyperopt_field_value``).
We import them directly to avoid duplicating logic. They are private
(underscore-prefixed) but reusing them here is intentional — both modules
operate on the same freqtrade strategy AST shape.
"""
from __future__ import annotations

import ast
import logging
from typing import Any

# Reuse private AST helpers from audit.py — see module docstring.
from app.freqtrade.audit import (
    _find_class_attr,
    _find_hyperopt_call,
    _find_strategy_class,
    _hyperopt_field_value,
    _literal_value,
    _parse_module,
)

logger = logging.getLogger(__name__)

UNPARSEABLE = "<unparseable>"

_HYPEROPT_KIND_NAMES = {
    "IntParameter",
    "DecimalParameter",
    "CategoricalParameter",
    "BooleanParameter",
}

_HYPEROPT_FIELDS = ("default", "low", "high", "decimals")

_COMPARE_OP_TO_STR: dict[type[ast.cmpop], str] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}


# ---------------------------------------------------------------------------
# Top-level field extractors
# ---------------------------------------------------------------------------


def extract_class_name(py: str) -> str | None:
    """Return the first ClassDef name in ``py``; ``None`` if no class.

    Returning ``None`` (not the sentinel) here matches the caller contract:
    the snapshot writer treats "no class found" as a hard failure to surface,
    not a per-field unparseable.
    """
    module = _parse_module(py)
    if module is None:
        logger.warning("strategy_extractor: failed to parse Python source for class name")
        return None
    cls = _find_strategy_class(module)
    if cls is None:
        return None
    return cls.name


def extract_timeframe(py: str) -> str:
    """Return the value of ``timeframe = "..."`` class attr, or sentinel."""
    cls = _find_class(py, field="timeframe")
    if cls is None:
        return UNPARSEABLE
    assign = _find_class_attr(cls, "timeframe")
    if assign is None:
        logger.warning("strategy_extractor: timeframe class attr not found")
        return UNPARSEABLE
    value = _literal_value(assign.value)
    if value == "<unparseable>" or not isinstance(value, str):
        logger.warning("strategy_extractor: timeframe value not a string literal")
        return UNPARSEABLE
    return value


def extract_stoploss(py: str) -> float | str:
    """Return the value of ``stoploss = ...`` class attr, or sentinel."""
    cls = _find_class(py, field="stoploss")
    if cls is None:
        return UNPARSEABLE
    assign = _find_class_attr(cls, "stoploss")
    if assign is None:
        logger.warning("strategy_extractor: stoploss class attr not found")
        return UNPARSEABLE
    value = _literal_value(assign.value)
    if value == "<unparseable>":
        logger.warning("strategy_extractor: stoploss value not a literal")
        return UNPARSEABLE
    if not isinstance(value, (int, float)):
        logger.warning("strategy_extractor: stoploss is not a numeric literal")
        return UNPARSEABLE
    return float(value)


def extract_minimal_roi(py: str) -> dict[str, float] | str:
    """Return the dict literal of ``minimal_roi = {...}``, or sentinel."""
    cls = _find_class(py, field="minimal_roi")
    if cls is None:
        return UNPARSEABLE
    assign = _find_class_attr(cls, "minimal_roi")
    if assign is None:
        logger.warning("strategy_extractor: minimal_roi class attr not found")
        return UNPARSEABLE
    if not isinstance(assign.value, ast.Dict):
        logger.warning("strategy_extractor: minimal_roi is not a dict literal")
        return UNPARSEABLE
    value = _literal_value(assign.value)
    if value == "<unparseable>" or not isinstance(value, dict):
        logger.warning("strategy_extractor: minimal_roi dict has non-literal entries")
        return UNPARSEABLE
    # Coerce keys to strings to keep snapshot rendering deterministic.
    return {str(k): v for k, v in value.items()}


def extract_hyperopt_params(py: str) -> dict[str, dict[str, Any]]:
    """Return ``{name: {kind, default, low, high, decimals}}`` for each
    hyperopt parameter assignment found in the class body.

    Each inner field falls back to :data:`UNPARSEABLE` when missing or
    non-literal. ``kind`` is the callee class name (e.g. ``"IntParameter"``);
    if the callee can't be resolved to one of the known hyperopt classes the
    parameter is skipped entirely (consistent with audit.py's behaviour).
    """
    cls = _find_class(py, field="hyperopt_params")
    if cls is None:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        name = stmt.targets[0].id
        call = _find_hyperopt_call(cls, name)
        if call is None:
            continue
        kind = _hyperopt_callee_name(call)
        if kind is None:
            # Belt-and-suspenders: _find_hyperopt_call already filters this,
            # but keep the explicit check so log messages are clear.
            logger.warning(
                "strategy_extractor: hyperopt param %s has unrecognised callee", name
            )
            continue
        fields: dict[str, Any] = {"kind": kind}
        for fname in _HYPEROPT_FIELDS:
            value = _hyperopt_field_value(call, fname)
            if value == "<unparseable>":
                # _hyperopt_field_value uses the same sentinel; surface it
                # under our public constant name and warn once.
                logger.warning(
                    "strategy_extractor: hyperopt param %s field %s unparseable",
                    name, fname,
                )
                fields[fname] = UNPARSEABLE
            else:
                fields[fname] = value
        out[name] = fields
    return out


def extract_entry_conditions(py: str) -> str:
    """Return a human-readable description of the entry condition tree.

    Walks the AST of ``populate_entry_trend`` looking for an assignment of
    the form ``dataframe.loc[<cond>, "enter_long"] = 1`` and renders ``<cond>``
    as a string like ``"rsi >= 35 AND close > ema20"``.

    Returns :data:`UNPARSEABLE` when the function is missing or its body
    doesn't match this pattern.
    """
    return _extract_loc_condition(py, "populate_entry_trend", "enter_long")


def extract_exit_conditions(py: str) -> str:
    """Return a human-readable description of the exit condition tree.

    Same shape as :func:`extract_entry_conditions` but matches
    ``dataframe.loc[<cond>, "exit_long"] = 1`` inside ``populate_exit_trend``.
    """
    return _extract_loc_condition(py, "populate_exit_trend", "exit_long")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_class(py: str, *, field: str) -> ast.ClassDef | None:
    """Parse ``py`` and return the first ClassDef, or None with a warning.

    ``field`` is included in the warning message so logs name the caller.
    """
    module = _parse_module(py)
    if module is None:
        logger.warning("strategy_extractor: failed to parse Python source (field=%s)", field)
        return None
    cls = _find_strategy_class(module)
    if cls is None:
        logger.warning("strategy_extractor: no strategy class found (field=%s)", field)
        return None
    return cls


def _hyperopt_callee_name(call: ast.Call) -> str | None:
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id if callee.id in _HYPEROPT_KIND_NAMES else None
    if isinstance(callee, ast.Attribute):
        return callee.attr if callee.attr in _HYPEROPT_KIND_NAMES else None
    return None


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for stmt in cls.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _extract_loc_condition(py: str, func_name: str, target_col: str) -> str:
    cls = _find_class(py, field=func_name)
    if cls is None:
        return UNPARSEABLE
    func = _find_method(cls, func_name)
    if func is None:
        logger.warning("strategy_extractor: %s not found", func_name)
        return UNPARSEABLE

    # Walk the function body looking for an assignment of the form:
    #   dataframe.loc[<cond>, "<target_col>"] = 1
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Subscript):
            continue
        if not _is_dataframe_loc(target.value):
            continue
        cond = _extract_loc_condition_node(target.slice, target_col)
        if cond is None:
            continue
        rendered = _render_condition(cond)
        if rendered == UNPARSEABLE:
            logger.warning(
                "strategy_extractor: %s condition tree is non-standard", func_name,
            )
        return rendered

    logger.warning(
        "strategy_extractor: %s has no dataframe.loc[..., %r] = 1 assignment",
        func_name, target_col,
    )
    return UNPARSEABLE


def _is_dataframe_loc(node: ast.AST) -> bool:
    """Match ``dataframe.loc`` (Attribute on a Name called ``dataframe``)."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "loc"
        and isinstance(node.value, ast.Name)
        and node.value.id == "dataframe"
    )


def _extract_loc_condition_node(slice_node: ast.AST, target_col: str) -> ast.AST | None:
    """Given the slice of ``dataframe.loc[...]``, return the condition node
    if the slice has shape ``(<cond>, "<target_col>")``; otherwise ``None``.
    """
    # On 3.9+ Subscript.slice is the expression directly (no ast.Index wrapper).
    if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
        cond, col = slice_node.elts
        if isinstance(col, ast.Constant) and col.value == target_col:
            return cond
    return None


def _render_condition(node: ast.AST) -> str:
    """Render an entry/exit condition AST as a human-readable string.

    Supports:
      - Compare with `<`, `<=`, `>`, `>=`, `==`, `!=`
      - BoolOp `and`/`or` AND bitwise BinOp `&`/`|` (freqtrade style)
      - Subscript on ``dataframe["col"]`` rendered as ``col``
      - Numeric/string literal constants
      - Attribute access (e.g. `self.rsi_period.value`) rendered dotted
      - Bare names

    Anything else returns :data:`UNPARSEABLE`. We propagate the sentinel up
    if any subtree is unrenderable so the caller sees a clean failure rather
    than a half-rendered string.
    """
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return UNPARSEABLE
        op_cls = type(node.ops[0])
        op_str = _COMPARE_OP_TO_STR.get(op_cls)
        if op_str is None:
            return UNPARSEABLE
        left = _render_operand(node.left)
        right = _render_operand(node.comparators[0])
        if left == UNPARSEABLE or right == UNPARSEABLE:
            return UNPARSEABLE
        return f"{left} {op_str} {right}"

    if isinstance(node, ast.BoolOp):
        joiner = "AND" if isinstance(node.op, ast.And) else (
            "OR" if isinstance(node.op, ast.Or) else None
        )
        if joiner is None:
            return UNPARSEABLE
        parts = [_render_condition(v) for v in node.values]
        if any(p == UNPARSEABLE for p in parts):
            return UNPARSEABLE
        return f" {joiner} ".join(_paren_if_boolop(p) for p in parts)

    # freqtrade conventionally uses `&` / `|` (bitwise) instead of `and`/`or`.
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.BitOr)):
        joiner = "AND" if isinstance(node.op, ast.BitAnd) else "OR"
        left = _render_condition(node.left)
        right = _render_condition(node.right)
        if left == UNPARSEABLE or right == UNPARSEABLE:
            return UNPARSEABLE
        return f"{_paren_if_boolop(left)} {joiner} {_paren_if_boolop(right)}"

    # A bare boolean expression as the loc index — render as-is when possible.
    operand = _render_operand(node)
    return operand


def _paren_if_boolop(rendered: str) -> str:
    """Add parentheses to clarify nested AND/OR rendering."""
    if " AND " in rendered or " OR " in rendered:
        return f"({rendered})"
    return rendered


def _render_operand(node: ast.AST) -> str:
    """Render a value (left/right of a Compare, etc.) as a string."""
    if isinstance(node, ast.Subscript) and _is_dataframe_subscript(node):
        col = _dataframe_subscript_column(node)
        return col if col is not None else UNPARSEABLE
    if isinstance(node, ast.Constant):
        return repr(node.value) if isinstance(node.value, str) else str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        rendered = _render_attribute_chain(node)
        return rendered if rendered is not None else UNPARSEABLE
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = "-" if isinstance(node.op, ast.USub) else "+"
        inner = _render_operand(node.operand)
        if inner == UNPARSEABLE:
            return UNPARSEABLE
        return f"{sign}{inner}"
    return UNPARSEABLE


def _is_dataframe_subscript(node: ast.Subscript) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "dataframe"


def _dataframe_subscript_column(node: ast.Subscript) -> str | None:
    slice_ = node.slice
    if isinstance(slice_, ast.Constant) and isinstance(slice_.value, str):
        return slice_.value
    return None


def _render_attribute_chain(node: ast.Attribute) -> str | None:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None
