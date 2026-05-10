"""
app/freqtrade/audit.py

Stage E audit module for the v2 revise pipeline.

Implements:

- :func:`deterministic_check_param`     — AST verification of param-type checklist items
- :func:`deterministic_check_invariants` — global invariants (timeframe / class name / order_types / look-ahead)
- :func:`check_unauthorized_changes`     — guard against subagent's silent edits outside checklist scope
- :func:`compute_subagent_self_report_consistent` — fill ``subagent_self_report_consistent`` per spec
- :func:`llm3_audit_logic`               — LLM3 call for ``type: logic`` items (rationale-stripped)
- :func:`run_audit`                       — top-level orchestration; returns :class:`AuditReport`
- :func:`write_audit_report`              — serialize to staging path

The contract source of truth is the openspec capability ``revise-checklist-protocol``.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.freqtrade.checklist import (
    CHECKLIST_AMBIGUOUS_TAG,
    UNAUTHORIZED_CHANGE_ID,
    AuditReport,
    Checklist,
    CheckResult,
    ChecklistItem,
    CompletionReport,
    ItemType,
    LogicTarget,
    Overall,
    ParamKind,
    ParamTarget,
    Verdict,
)

logger = logging.getLogger(__name__)

# Sentinel evidence string used when AST extraction fails.
_UNPARSEABLE = "<unparseable>"

_HYPEROPT_PARAM_CALLEES = {"IntParameter", "DecimalParameter", "CategoricalParameter", "BooleanParameter"}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse_module(py_source: str) -> ast.Module | None:
    try:
        return ast.parse(py_source)
    except SyntaxError as e:
        logger.warning("audit: failed to parse Python source: %s", e)
        return None


def _find_strategy_class(module: ast.Module) -> ast.ClassDef | None:
    """Return the first ClassDef in a module (heuristic: strategy file has one class)."""
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            return node
    return None


def _literal_value(node: ast.AST) -> Any:
    """Best-effort literal evaluation of an AST expression node.

    Returns the literal value, or :data:`_UNPARSEABLE` sentinel when not a
    plain literal.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return _UNPARSEABLE


def _find_class_attr(class_node: ast.ClassDef, name: str) -> ast.Assign | None:
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return stmt
    return None


def _find_hyperopt_call(class_node: ast.ClassDef, name: str) -> ast.Call | None:
    """Find ``<name> = IntParameter/DecimalParameter/CategoricalParameter(...)`` in class body."""
    assign = _find_class_attr(class_node, name)
    if assign is None:
        return None
    if not isinstance(assign.value, ast.Call):
        return None
    callee = assign.value.func
    callee_name: str | None = None
    if isinstance(callee, ast.Name):
        callee_name = callee.id
    elif isinstance(callee, ast.Attribute):
        callee_name = callee.attr
    if callee_name not in _HYPEROPT_PARAM_CALLEES:
        return None
    return assign.value


def _hyperopt_field_value(call: ast.Call, field_name: str) -> Any:
    """Extract ``field_name`` argument from a hyperopt parameter call.

    ``default`` may be passed positionally (3rd positional arg in IntParameter)
    or as a keyword. Other fields (``low``/``high``/``decimals``) are typically
    keyword-only or in known positional slots.
    """
    # keyword args first
    for kw in call.keywords:
        if kw.arg == field_name:
            return _literal_value(kw.value)

    # positional fallback for IntParameter / DecimalParameter:
    #   IntParameter(low, high, default=, space=, optimize=, decimals=)
    if field_name == "low" and len(call.args) >= 1:
        return _literal_value(call.args[0])
    if field_name == "high" and len(call.args) >= 2:
        return _literal_value(call.args[1])
    if field_name == "default" and len(call.args) >= 3:
        return _literal_value(call.args[2])

    return _UNPARSEABLE


def _resolve_dict_path(class_node: ast.ClassDef, path: str) -> Any:
    """Resolve a dotted path like ``minimal_roi."0"`` to a literal value.

    Path syntax: first segment is a class attribute name (a dict literal),
    subsequent segments are keys (quoted = string, bare = same as quoted).
    """
    # primitive lexer: split on '.' but respect quoted segments
    parts: list[str] = []
    buf = ""
    in_quote: str | None = None
    for ch in path:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            else:
                buf += ch
            continue
        if ch in ('"', "'"):
            in_quote = ch
            continue
        if ch == ".":
            if buf:
                parts.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    if not parts:
        return _UNPARSEABLE

    head, *tail = parts
    assign = _find_class_attr(class_node, head)
    if assign is None or not isinstance(assign.value, ast.Dict):
        return _UNPARSEABLE
    current_dict = assign.value
    value: Any = _literal_value(current_dict)
    if value is _UNPARSEABLE:
        return _UNPARSEABLE
    for seg in tail:
        if not isinstance(value, dict):
            return _UNPARSEABLE
        if seg in value:
            value = value[seg]
        else:
            return _UNPARSEABLE
    return value


# ---------------------------------------------------------------------------
# 1.6 deterministic_check_param
# ---------------------------------------------------------------------------


def deterministic_check_param(item: ChecklistItem, new_py_path: str | os.PathLike) -> CheckResult:
    """Verify a single param-type checklist item against the new .py.

    Returns a :class:`CheckResult` with verdict PASS or FAIL. ``subagent_self_report_consistent``
    is left as ``None`` here; :func:`compute_subagent_self_report_consistent` populates it later.
    """
    if item.type != ItemType.PARAM:
        raise ValueError(f"deterministic_check_param: item {item.id} is not type=param")
    target = item.target
    if not isinstance(target, ParamTarget):
        raise ValueError(f"deterministic_check_param: item {item.id} target is not ParamTarget")

    src = Path(new_py_path).read_text(encoding="utf-8")
    module = _parse_module(src)
    if module is None:
        return CheckResult(id=item.id, verdict=Verdict.FAIL,
                           evidence=f"new .py syntax error; cannot parse {new_py_path}")
    cls = _find_strategy_class(module)
    if cls is None:
        return CheckResult(id=item.id, verdict=Verdict.FAIL,
                           evidence="new .py has no strategy class")

    expected = item.to_value
    actual: Any
    line: int | None = None

    if target.kind == ParamKind.CLASS_ATTR:
        assign = _find_class_attr(cls, target.name)
        if assign is None:
            return CheckResult(id=item.id, verdict=Verdict.FAIL,
                               evidence=f"class attr '{target.name}' not found")
        actual = _literal_value(assign.value)
        line = assign.lineno

    elif target.kind == ParamKind.HYPEROPT_PARAM:
        call = _find_hyperopt_call(cls, target.name)
        if call is None:
            return CheckResult(id=item.id, verdict=Verdict.FAIL,
                               evidence=f"hyperopt param '{target.name}' not found "
                                        f"(expected IntParameter/DecimalParameter/CategoricalParameter call)")
        actual = _hyperopt_field_value(call, target.field or "default")
        line = call.lineno

    elif target.kind == ParamKind.DICT_VALUE:
        if target.path is None:
            return CheckResult(id=item.id, verdict=Verdict.FAIL,
                               evidence="dict_value target.path is None")
        actual = _resolve_dict_path(cls, target.path)
    else:  # pragma: no cover — exhausted by enum
        return CheckResult(id=item.id, verdict=Verdict.FAIL,
                           evidence=f"unknown ParamKind: {target.kind}")

    if actual is _UNPARSEABLE:
        return CheckResult(id=item.id, verdict=Verdict.FAIL,
                           evidence=f"could not extract current value of '{target.name}' from .py "
                                    f"(non-literal expression)")

    where = f"{Path(new_py_path).name}:{line}" if line else Path(new_py_path).name
    if actual == expected:
        return CheckResult(id=item.id, verdict=Verdict.PASS,
                           evidence=f"{where} {target.name}={actual!r} matches checklist.to")
    return CheckResult(
        id=item.id, verdict=Verdict.FAIL,
        evidence=f"{where} {target.name}={actual!r} does not match checklist.to={expected!r}",
    )


# ---------------------------------------------------------------------------
# 1.7 deterministic_check_invariants
# ---------------------------------------------------------------------------


def _extract_timeframe(class_node: ast.ClassDef) -> Any:
    assign = _find_class_attr(class_node, "timeframe")
    if assign is None:
        return _UNPARSEABLE
    return _literal_value(assign.value)


def _extract_order_types_keys(class_node: ast.ClassDef) -> set[str] | None:
    assign = _find_class_attr(class_node, "order_types")
    if assign is None or not isinstance(assign.value, ast.Dict):
        return None
    keys: set[str] = set()
    for k in assign.value.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)
    return keys


_LOOKAHEAD_PATTERN = re.compile(r"\.shift\(\s*-\s*\d+\s*\)")


def deterministic_check_invariants(
    invariants: list[str], old_py: str, new_py: str,
) -> list[CheckResult]:
    """Run global invariant checks on old vs new .py source.

    All invariant results have ``subagent_self_report_consistent = None``
    (no corresponding checklist item id; spec rule).
    """
    results: list[CheckResult] = []
    new_module = _parse_module(new_py)
    old_module = _parse_module(old_py)

    if new_module is None:
        results.append(CheckResult(
            id="syntax_error", verdict=Verdict.FAIL,
            evidence="new .py has syntax error", subagent_self_report_consistent=None,
        ))
        return results

    new_cls = _find_strategy_class(new_module)
    old_cls = _find_strategy_class(old_module) if old_module else None

    for inv in invariants:
        if inv == "timeframe_unchanged":
            if new_cls is None:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence="new .py has no strategy class",
                    subagent_self_report_consistent=None,
                ))
                continue
            new_tf = _extract_timeframe(new_cls)
            old_tf = _extract_timeframe(old_cls) if old_cls else _UNPARSEABLE
            if old_tf is _UNPARSEABLE or new_tf is _UNPARSEABLE:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence=f"timeframe unparseable (old={old_tf!r} new={new_tf!r})",
                    subagent_self_report_consistent=None,
                ))
            elif old_tf == new_tf:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.PASS,
                    evidence=f"timeframe={new_tf!r} unchanged",
                    subagent_self_report_consistent=None,
                ))
            else:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence=f"timeframe changed: old={old_tf!r} new={new_tf!r}",
                    subagent_self_report_consistent=None,
                ))

        elif inv == "class_name_unchanged":
            if new_cls is None or old_cls is None:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence="cannot locate strategy class in old or new .py",
                    subagent_self_report_consistent=None,
                ))
                continue
            if old_cls.name == new_cls.name:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.PASS,
                    evidence=f"class name '{new_cls.name}' unchanged",
                    subagent_self_report_consistent=None,
                ))
            else:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence=f"class name changed: old='{old_cls.name}' new='{new_cls.name}'",
                    subagent_self_report_consistent=None,
                ))

        elif inv == "order_types_four_keys":
            required_keys = {"entry", "exit", "stoploss", "stoploss_on_exchange"}
            if new_cls is None:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence="no strategy class in new .py",
                    subagent_self_report_consistent=None,
                ))
                continue
            keys = _extract_order_types_keys(new_cls)
            if keys is None:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence="order_types is missing or not a dict literal",
                    subagent_self_report_consistent=None,
                ))
            elif required_keys.issubset(keys):
                results.append(CheckResult(
                    id=inv, verdict=Verdict.PASS,
                    evidence=f"order_types has all 4 required keys: {sorted(required_keys)}",
                    subagent_self_report_consistent=None,
                ))
            else:
                missing = required_keys - keys
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence=f"order_types missing required keys: {sorted(missing)}",
                    subagent_self_report_consistent=None,
                ))

        elif inv == "no_lookahead_pattern":
            # naive textual check: forbid .shift(-N) in the new .py source
            offenders: list[int] = []
            for lineno, line in enumerate(new_py.splitlines(), start=1):
                if _LOOKAHEAD_PATTERN.search(line):
                    offenders.append(lineno)
            if offenders:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.FAIL,
                    evidence=f"look-ahead pattern .shift(-N) at lines {offenders}",
                    subagent_self_report_consistent=None,
                ))
            else:
                results.append(CheckResult(
                    id=inv, verdict=Verdict.PASS,
                    evidence="no .shift(-N) pattern detected",
                    subagent_self_report_consistent=None,
                ))
        else:
            logger.warning("audit: unknown invariant '%s' — skipping", inv)
    return results


# ---------------------------------------------------------------------------
# 1.18 derive_authorized_targets
# ---------------------------------------------------------------------------


@dataclass
class AuthorizedTargets:
    """Derived from a checklist; defines what AST regions Stage D may modify."""

    class_attrs: set[str] = field(default_factory=set)
    hyperopt_params: set[str] = field(default_factory=set)
    dict_attrs: set[str] = field(default_factory=set)  # for dict_value: only the head attr
    functions: set[str] = field(default_factory=set)


def derive_authorized_targets(checklist: Checklist) -> AuthorizedTargets:
    """Compute the AST-level write whitelist from a checklist."""
    out = AuthorizedTargets()
    for item in checklist.items:
        if item.type == ItemType.PARAM and isinstance(item.target, ParamTarget):
            tgt = item.target
            if tgt.kind == ParamKind.CLASS_ATTR:
                out.class_attrs.add(tgt.name)
            elif tgt.kind == ParamKind.HYPEROPT_PARAM:
                out.hyperopt_params.add(tgt.name)
            elif tgt.kind == ParamKind.DICT_VALUE:
                # the head attribute of the path is authorized
                if tgt.path:
                    head = tgt.path.split(".", 1)[0]
                    out.dict_attrs.add(head)
        elif item.type == ItemType.LOGIC and isinstance(item.target, LogicTarget):
            out.functions.add(item.target.function)
    return out


# ---------------------------------------------------------------------------
# 1.17 + 1.23 check_unauthorized_changes
# ---------------------------------------------------------------------------


def _ast_signature(node: ast.AST) -> str:
    """Stable structural fingerprint of an AST subtree (whitespace/comment-invariant)."""
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _get_class_docstring(class_node: ast.ClassDef) -> str | None:
    return ast.get_docstring(class_node, clean=False)


def _function_signature(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Body-only AST signature ignoring docstring (docstring checked separately).

    Returns a string for stable comparison.
    """
    body = list(func_node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return ";".join(_ast_signature(stmt) for stmt in body)


def check_unauthorized_changes(
    old_py: str, new_py: str, checklist: Checklist,
) -> CheckResult | None:
    """Detect structural changes outside the authorized whitelist.

    Returns a :class:`CheckResult` with ``id == UNAUTHORIZED_CHANGE_ID`` and
    ``verdict == FAIL`` when an unauthorized change is detected. Returns
    ``None`` (not a PASS result) when no unauthorized change exists.

    ``subagent_self_report_consistent`` is always ``None`` (this check has no
    matching checklist item id).
    """
    authorized = derive_authorized_targets(checklist)

    new_module = _parse_module(new_py)
    old_module = _parse_module(old_py)
    if new_module is None or old_module is None:
        return CheckResult(
            id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
            evidence="cannot parse old or new .py for unauthorized-change check",
            subagent_self_report_consistent=None,
        )

    new_cls = _find_strategy_class(new_module)
    old_cls = _find_strategy_class(old_module)
    if new_cls is None or old_cls is None:
        return CheckResult(
            id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
            evidence="strategy class not found in old or new .py",
            subagent_self_report_consistent=None,
        )

    # docstring change detection (always unauthorized per spec)
    if _get_class_docstring(old_cls) != _get_class_docstring(new_cls):
        return CheckResult(
            id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
            evidence="class docstring modified (docstrings are never authorized)",
            subagent_self_report_consistent=None,
        )

    # build maps of class-body statements keyed by element kind
    def _index_class(cls: ast.ClassDef) -> tuple[
        dict[str, ast.Assign],            # name -> attribute assignment
        dict[str, ast.FunctionDef | ast.AsyncFunctionDef],  # name -> method
        list[ast.AST],                    # other stmts (rare: class-level statements other than assigns/defs)
    ]:
        attrs: dict[str, ast.Assign] = {}
        methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        others: list[ast.AST] = []
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                attrs[stmt.targets[0].id] = stmt
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[stmt.name] = stmt
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                # class-level docstring; handled separately
                pass
            else:
                others.append(stmt)
        return attrs, methods, others

    old_attrs, old_methods, old_others = _index_class(old_cls)
    new_attrs, new_methods, new_others = _index_class(new_cls)

    # 1) class attribute changes — must be in authorized whitelist
    authorized_attr_names = (
        authorized.class_attrs | authorized.hyperopt_params | authorized.dict_attrs
    )
    all_attr_names = set(old_attrs.keys()) | set(new_attrs.keys())
    for name in all_attr_names:
        old_node = old_attrs.get(name)
        new_node = new_attrs.get(name)
        if old_node is None or new_node is None:
            # added or removed attribute
            if name not in authorized_attr_names:
                return CheckResult(
                    id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
                    evidence=f"class attribute '{name}' "
                             f"{'added' if old_node is None else 'removed'} "
                             f"but not in checklist",
                    subagent_self_report_consistent=None,
                )
            continue
        if _ast_signature(old_node) != _ast_signature(new_node):
            if name not in authorized_attr_names:
                return CheckResult(
                    id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
                    evidence=f"class attribute '{name}' modified but not in checklist",
                    subagent_self_report_consistent=None,
                )

    # 2) method changes — body must be unchanged unless function is authorized
    all_method_names = set(old_methods.keys()) | set(new_methods.keys())
    for name in all_method_names:
        old_node = old_methods.get(name)
        new_node = new_methods.get(name)
        if old_node is None or new_node is None:
            if name not in authorized.functions:
                return CheckResult(
                    id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
                    evidence=f"method '{name}' "
                             f"{'added' if old_node is None else 'removed'} "
                             f"but not in checklist",
                    subagent_self_report_consistent=None,
                )
            continue
        if name in authorized.functions:
            continue
        # function docstring is also part of "body" but check it strictly
        if ast.get_docstring(old_node, clean=False) != ast.get_docstring(new_node, clean=False):
            return CheckResult(
                id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
                evidence=f"method '{name}' docstring modified (docstrings never authorized)",
                subagent_self_report_consistent=None,
            )
        if _function_signature(old_node) != _function_signature(new_node):
            return CheckResult(
                id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
                evidence=f"method '{name}' body modified but not in checklist",
                subagent_self_report_consistent=None,
            )

    # 3) other class-body statements (rare; treat any change as unauthorized)
    if [_ast_signature(s) for s in old_others] != [_ast_signature(s) for s in new_others]:
        return CheckResult(
            id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL,
            evidence="non-attribute, non-method class body statements changed",
            subagent_self_report_consistent=None,
        )

    return None


# ---------------------------------------------------------------------------
# 1.14 compute_subagent_self_report_consistent
# ---------------------------------------------------------------------------


def compute_subagent_self_report_consistent(
    deterministic_results: list[CheckResult],
    llm3_results: list[CheckResult],
    completion_report: CompletionReport,
    checklist: Checklist,
) -> tuple[list[CheckResult], list[CheckResult]]:
    """Populate ``subagent_self_report_consistent`` per the spec rules.

    Returns the same lists with the field filled (objects are mutated and returned).

    Rules:
    - For deterministic results whose ``id`` matches a checklist item id and that
      item is reported ``completed: true``: ``False`` if FAIL, ``True`` if PASS.
    - For deterministic results whose ``id`` is an invariant or
      ``unauthorized_change`` (i.e. not in checklist item ids): ``None``.
    - For LLM3 results whose ``id`` matches a checklist item id and that item
      is ``completed: true``: ``False`` if FAIL or INSUFFICIENT, ``True`` if PASS.
    """
    item_ids = {it.id for it in checklist.items}
    completed_map = {it.id: it.completed for it in completion_report.items}

    def _flag(r: CheckResult) -> bool | None:
        if r.id not in item_ids:
            return None
        completed = completed_map.get(r.id, False)
        if not completed:
            # subagent self-reported NOT completed; not dishonest by definition
            return True
        if r.verdict in (Verdict.FAIL, Verdict.INSUFFICIENT):
            return False
        return True

    for r in deterministic_results:
        r.subagent_self_report_consistent = _flag(r)
    for r in llm3_results:
        r.subagent_self_report_consistent = _flag(r)
    return deterministic_results, llm3_results


# ---------------------------------------------------------------------------
# 1.8 llm3_audit_logic
# ---------------------------------------------------------------------------


_LLM3_PROMPT_NAME = "revise_audit"


def _strip_logic_item_for_llm3(item: ChecklistItem) -> dict:
    """Return a dict with rationale removed and only LLM3-permitted fields."""
    if item.type != ItemType.LOGIC or not isinstance(item.target, LogicTarget):
        raise ValueError(f"strip_logic_item_for_llm3: item {item.id} is not type=logic")
    return {
        "id": item.id,
        "type": item.type.value,
        "target": {"function": item.target.function},
        "expected_signals": list(item.expected_signals),
        "forbidden_signals": list(item.forbidden_signals),
        "depends_on": list(item.depends_on),
    }


def _load_llm3_prompt() -> str:
    prompts_dir = Path(__file__).parent.parent / "prompts" / "freqtrade"
    return (prompts_dir / f"{_LLM3_PROMPT_NAME}.txt").read_text(encoding="utf-8")


def llm3_audit_logic(
    items: list[ChecklistItem],
    old_py: str,
    new_py: str,
    completion_report: CompletionReport,
    output_dir: str,
    *,
    call_llm: callable | None = None,
) -> list[CheckResult]:
    """Run LLM3 audit on the logic-typed items only.

    The ``call_llm`` parameter exists for testability; production callers should
    leave it ``None`` so the default freqtrade ``_call_llm`` is used.

    Returns a list of CheckResult objects, one per input item. Each result has
    its ``subagent_self_report_consistent`` left as ``None`` here (the
    orchestrator will populate it via :func:`compute_subagent_self_report_consistent`).
    """
    if not items:
        return []

    # Resolve LLM caller lazily to avoid pulling steps.py at import time
    if call_llm is None:
        from app.freqtrade.steps import _call_llm  # type: ignore
        call_llm = _call_llm

    sanitized = [_strip_logic_item_for_llm3(it) for it in items]
    completion_dump = {
        "iteration": completion_report.iteration,
        "attempt": completion_report.attempt,
        "items": [
            {"id": it.id, "completed": it.completed, "location": it.location}
            for it in completion_report.items
        ],
        "unimplementable_items": list(completion_report.unimplementable_items),
    }

    prompt = _load_llm3_prompt().format(
        ITEMS=json.dumps(sanitized, ensure_ascii=False, indent=2),
        OLD_PY=old_py,
        NEW_PY=new_py,
        COMPLETION_REPORT=json.dumps(completion_dump, ensure_ascii=False, indent=2),
        OUTPUT_DIR=output_dir,
    )

    try:
        call_llm(prompt, cwd=output_dir)
    except Exception as e:
        logger.warning("audit: LLM3 call failed (%s); marking all logic items INSUFFICIENT", e)
        return [
            CheckResult(id=it.id, verdict=Verdict.INSUFFICIENT,
                        evidence=f"LLM3 unavailable: {e}",
                        subagent_self_report_consistent=None)
            for it in items
        ]

    # LLM3 writes its results to OUTPUT_DIR/llm3_audit_result.yaml
    result_path = Path(output_dir) / "llm3_audit_result.yaml"
    if not result_path.exists():
        logger.warning("audit: LLM3 produced no llm3_audit_result.yaml; INSUFFICIENT for all")
        return [
            CheckResult(id=it.id, verdict=Verdict.INSUFFICIENT,
                        evidence="LLM3 produced no result file",
                        subagent_self_report_consistent=None)
            for it in items
        ]

    import yaml as _yaml
    try:
        raw = _yaml.safe_load(result_path.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError as e:
        logger.warning("audit: LLM3 result YAML parse failed: %s", e)
        return [
            CheckResult(id=it.id, verdict=Verdict.INSUFFICIENT,
                        evidence=f"LLM3 result YAML parse failed: {e}",
                        subagent_self_report_consistent=None)
            for it in items
        ]

    raw_items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(raw_items, list):
        return [
            CheckResult(id=it.id, verdict=Verdict.INSUFFICIENT,
                        evidence="LLM3 result missing 'items' list",
                        subagent_self_report_consistent=None)
            for it in items
        ]

    by_id = {r.get("id"): r for r in raw_items if isinstance(r, dict)}
    results: list[CheckResult] = []
    for item in items:
        r = by_id.get(item.id)
        if not r:
            results.append(CheckResult(
                id=item.id, verdict=Verdict.INSUFFICIENT,
                evidence="LLM3 did not return a verdict for this item",
                subagent_self_report_consistent=None,
            ))
            continue
        try:
            verdict = Verdict(r.get("verdict"))
        except ValueError:
            verdict = Verdict.INSUFFICIENT
        evidence = str(r.get("evidence", "")) or "LLM3 returned no evidence"
        suggested_fix = r.get("suggested_fix")
        results.append(CheckResult(
            id=item.id, verdict=verdict, evidence=evidence,
            suggested_fix=str(suggested_fix) if suggested_fix else None,
            subagent_self_report_consistent=None,
        ))
    return results


# ---------------------------------------------------------------------------
# 1.9 run_audit + 1.9a write_audit_report
# ---------------------------------------------------------------------------


def _classify_overall(
    deterministic: list[CheckResult], llm3: list[CheckResult],
) -> tuple[Overall, str | None]:
    if any(r.verdict == Verdict.FAIL for r in deterministic):
        # collect failing ids
        failing_ids = [r.id for r in deterministic if r.verdict == Verdict.FAIL]
        return Overall.REJECTED, f"deterministic FAIL: {failing_ids}"
    if any(r.verdict == Verdict.FAIL for r in llm3):
        failing_ids = [r.id for r in llm3 if r.verdict == Verdict.FAIL]
        return Overall.REJECTED, f"LLM3 FAIL: {failing_ids}"
    if any(r.verdict == Verdict.INSUFFICIENT for r in llm3):
        ambiguous_ids = [r.id for r in llm3 if r.verdict == Verdict.INSUFFICIENT]
        return Overall.REJECTED, f"{CHECKLIST_AMBIGUOUS_TAG}: {ambiguous_ids}"
    return Overall.APPROVED, None


def run_audit(
    checklist: Checklist,
    new_py_path: str | os.PathLike,
    old_py_path: str | os.PathLike,
    completion_report: CompletionReport,
    output_dir: str,
    *,
    iteration: int | None = None,
    attempt: int = 0,
    call_llm: callable | None = None,
) -> AuditReport:
    """Top-level Stage E orchestration.

    Order of checks:
      1. Deterministic invariants (timeframe, class name, order_types, look-ahead)
      2. Deterministic param-item checks (one per ``type: param`` checklist item)
      3. Unauthorized-change guard (against checklist whitelist)
      4. If anything FAIL'd above, short-circuit (skip LLM3); else run LLM3 on logic items
      5. Compute ``subagent_self_report_consistent`` for all results
      6. Compute overall verdict + reject_summary
    """
    iteration = iteration if iteration is not None else checklist.iteration

    new_py = Path(new_py_path).read_text(encoding="utf-8")
    old_py = Path(old_py_path).read_text(encoding="utf-8")

    # 1) invariants
    invariant_results = deterministic_check_invariants(checklist.invariants, old_py, new_py)

    # 2) param items
    param_results: list[CheckResult] = []
    for item in checklist.items:
        if item.type == ItemType.PARAM:
            param_results.append(deterministic_check_param(item, new_py_path))

    # 3) unauthorized change
    unauth_results: list[CheckResult] = []
    unauth = check_unauthorized_changes(old_py, new_py, checklist)
    if unauth is not None:
        unauth_results.append(unauth)

    deterministic_results = invariant_results + param_results + unauth_results

    # 4) LLM3 — only if deterministic is clean
    llm3_results: list[CheckResult] = []
    short_circuit = any(r.verdict == Verdict.FAIL for r in deterministic_results)
    if not short_circuit:
        logic_items = [it for it in checklist.items if it.type == ItemType.LOGIC]
        llm3_results = llm3_audit_logic(
            logic_items, old_py, new_py, completion_report, output_dir, call_llm=call_llm,
        )

    # 5) consistency flags
    deterministic_results, llm3_results = compute_subagent_self_report_consistent(
        deterministic_results, llm3_results, completion_report, checklist,
    )

    # 6) overall
    overall, reject_summary = _classify_overall(deterministic_results, llm3_results)

    return AuditReport(
        iteration=iteration,
        attempt=attempt,
        deterministic_results=deterministic_results,
        llm3_results=llm3_results,
        overall=overall,
        reject_summary=reject_summary,
    )


def _check_result_to_dict(r: CheckResult) -> dict:
    out: dict = {
        "id": r.id,
        "verdict": r.verdict.value,
        "evidence": r.evidence,
        "subagent_self_report_consistent": r.subagent_self_report_consistent,
    }
    if r.suggested_fix:
        out["suggested_fix"] = r.suggested_fix
    return out


def write_audit_report(report: AuditReport, output_path: str | os.PathLike) -> Path:
    """Serialize an AuditReport to YAML at ``output_path``.

    Used by the orchestrator to persist each Stage E attempt to
    ``artifacts/.staging/v{N}/audit_report_attempt_{k}.yaml`` (task 1.9a).
    """
    import yaml as _yaml
    payload = {
        "iteration": report.iteration,
        "attempt": report.attempt,
        "deterministic_results": [_check_result_to_dict(r) for r in report.deterministic_results],
        "llm3_results": [_check_result_to_dict(r) for r in report.llm3_results],
        "overall": report.overall.value,
    }
    if report.reject_summary:
        payload["reject_summary"] = report.reject_summary
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return out
