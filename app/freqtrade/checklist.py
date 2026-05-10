"""
app/freqtrade/checklist.py

Schema dataclasses and parsers for the v2 revise pipeline contracts.

Source of truth for the openspec capability `revise-checklist-protocol`:

- ``checklist.yaml``         (LLM2 → subagent + audit)        → :class:`Checklist`
- ``completion_report.yaml`` (subagent → audit)               → :class:`CompletionReport`
- ``audit_report.yaml``      (audit → orchestrator)           → :class:`AuditReport`

The orchestrator in ``app/freqtrade/steps.py`` consumes these objects via
:func:`cross_check` to decide between three failure routes:
``UNIMPLEMENTABLE_CHECKLIST`` / ``IMPLEMENTATION_FAILED`` / ``READY_FOR_AUDIT``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml


class ValidationError(ValueError):
    """Raised when a contract YAML fails schema validation."""


# ---------------------------------------------------------------------------
# checklist.yaml
# ---------------------------------------------------------------------------


class ItemType(str, Enum):
    PARAM = "param"
    LOGIC = "logic"


class ParamKind(str, Enum):
    CLASS_ATTR = "class_attr"
    HYPEROPT_PARAM = "hyperopt_param"
    DICT_VALUE = "dict_value"


@dataclass
class ParamTarget:
    kind: ParamKind
    name: str
    field: str | None = None  # only for hyperopt_param: default | low | high | decimals
    path: str | None = None   # only for dict_value: e.g. 'minimal_roi."0"'


@dataclass
class LogicTarget:
    function: str  # e.g. populate_indicators


@dataclass
class ChecklistItem:
    """Single modification item in checklist.yaml.

    Either ``target`` is a :class:`ParamTarget` (when ``type == PARAM``) and
    ``from_value`` / ``to_value`` are required, or ``target`` is a
    :class:`LogicTarget` (when ``type == LOGIC``) and ``expected_signals`` /
    ``forbidden_signals`` are required.
    """

    id: str
    type: ItemType
    target: ParamTarget | LogicTarget
    rationale: str
    # type == PARAM
    from_value: Any = None
    to_value: Any = None
    # type == LOGIC
    expected_signals: list[str] = field(default_factory=list)
    forbidden_signals: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Checklist:
    iteration: int
    based_on_intent: str
    created_by: str
    locked: bool
    invariants: list[str]
    items: list[ChecklistItem]


_REQUIRED_INVARIANTS = {
    "timeframe_unchanged",
    "class_name_unchanged",
    "order_types_four_keys",
    "no_lookahead_pattern",
}


def _require(d: dict, key: str, ctx: str) -> Any:
    if key not in d:
        raise ValidationError(f"{ctx}: missing required field '{key}'")
    return d[key]


def _require_type(value: Any, expected_type: type, ctx: str, field_name: str) -> Any:
    if not isinstance(value, expected_type):
        raise ValidationError(
            f"{ctx}: field '{field_name}' must be {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def _parse_param_target(raw: dict, ctx: str) -> ParamTarget:
    kind_raw = _require(raw, "kind", ctx)
    try:
        kind = ParamKind(kind_raw)
    except ValueError:
        raise ValidationError(
            f"{ctx}: target.kind '{kind_raw}' must be one of "
            f"{[k.value for k in ParamKind]}"
        )
    name = _require_type(_require(raw, "name", ctx), str, ctx, "target.name")

    field_val: str | None = raw.get("field")
    path_val: str | None = raw.get("path")

    if kind == ParamKind.HYPEROPT_PARAM:
        if not field_val:
            raise ValidationError(f"{ctx}: target.field required when kind=hyperopt_param")
        if field_val not in {"default", "low", "high", "decimals"}:
            raise ValidationError(
                f"{ctx}: target.field '{field_val}' must be default|low|high|decimals"
            )
    if kind == ParamKind.DICT_VALUE:
        if not path_val:
            raise ValidationError(f"{ctx}: target.path required when kind=dict_value")
    return ParamTarget(kind=kind, name=name, field=field_val, path=path_val)


def _parse_logic_target(raw: dict, ctx: str) -> LogicTarget:
    fn = _require_type(_require(raw, "function", ctx), str, ctx, "target.function")
    return LogicTarget(function=fn)


def _parse_checklist_item(raw: dict, idx: int) -> ChecklistItem:
    ctx = f"checklist.items[{idx}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{ctx}: must be a mapping, got {type(raw).__name__}")

    item_id = _require_type(_require(raw, "id", ctx), str, ctx, "id")
    type_raw = _require(raw, "type", ctx)
    try:
        item_type = ItemType(type_raw)
    except ValueError:
        raise ValidationError(
            f"{ctx}: type '{type_raw}' must be one of {[t.value for t in ItemType]}"
        )
    rationale = _require_type(_require(raw, "rationale", ctx), str, ctx, "rationale")
    target_raw = _require(raw, "target", ctx)
    if not isinstance(target_raw, dict):
        raise ValidationError(f"{ctx}.target: must be a mapping")

    if item_type == ItemType.PARAM:
        target: ParamTarget | LogicTarget = _parse_param_target(target_raw, f"{ctx}.target")
        if "from" not in raw:
            raise ValidationError(f"{ctx}: missing required field 'from'")
        if "to" not in raw:
            raise ValidationError(f"{ctx}: missing required field 'to'")
        return ChecklistItem(
            id=item_id, type=item_type, target=target, rationale=rationale,
            from_value=raw["from"], to_value=raw["to"],
        )

    # ItemType.LOGIC
    target = _parse_logic_target(target_raw, f"{ctx}.target")
    expected_raw = _require(raw, "expected_signals", ctx)
    if not isinstance(expected_raw, list) or not expected_raw:
        raise ValidationError(
            f"{ctx}.expected_signals: must be a non-empty list (logic items require at least 1)"
        )
    forbidden_raw = raw.get("forbidden_signals", [])
    if not isinstance(forbidden_raw, list):
        raise ValidationError(f"{ctx}.forbidden_signals: must be a list (may be empty)")
    depends_on = raw.get("depends_on", [])
    if not isinstance(depends_on, list):
        raise ValidationError(f"{ctx}.depends_on: must be a list")
    return ChecklistItem(
        id=item_id, type=item_type, target=target, rationale=rationale,
        expected_signals=[str(s) for s in expected_raw],
        forbidden_signals=[str(s) for s in forbidden_raw],
        depends_on=[str(s) for s in depends_on],
    )


def parse_checklist(yaml_str: str) -> Checklist:
    """Parse and validate a checklist.yaml string. Raises ValidationError."""
    try:
        raw = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValidationError(f"checklist.yaml: invalid YAML: {e}")
    if not isinstance(raw, dict):
        raise ValidationError("checklist.yaml: top-level must be a mapping")

    ctx = "checklist"
    iteration = _require_type(_require(raw, "iteration", ctx), int, ctx, "iteration")
    based_on_intent = _require_type(_require(raw, "based_on_intent", ctx), str, ctx, "based_on_intent")
    created_by = _require_type(_require(raw, "created_by", ctx), str, ctx, "created_by")
    locked = _require_type(_require(raw, "locked", ctx), bool, ctx, "locked")
    if not locked:
        raise ValidationError("checklist.yaml: locked SHALL be true (Stage C contract)")

    invariants_raw = _require(raw, "invariants", ctx)
    if not isinstance(invariants_raw, list) or not all(isinstance(v, str) for v in invariants_raw):
        raise ValidationError("checklist.invariants: must be a list of strings")
    missing = _REQUIRED_INVARIANTS - set(invariants_raw)
    if missing:
        raise ValidationError(f"checklist.invariants: missing required invariants {sorted(missing)}")

    items_raw = _require(raw, "items", ctx)
    if not isinstance(items_raw, list) or not items_raw:
        raise ValidationError("checklist.items: must be a non-empty list")

    items = [_parse_checklist_item(it, idx) for idx, it in enumerate(items_raw)]

    # uniqueness check on item ids
    seen: set[str] = set()
    for it in items:
        if it.id in seen:
            raise ValidationError(f"checklist.items: duplicate id '{it.id}'")
        seen.add(it.id)

    return Checklist(
        iteration=iteration,
        based_on_intent=based_on_intent,
        created_by=created_by,
        locked=locked,
        invariants=list(invariants_raw),
        items=items,
    )


# ---------------------------------------------------------------------------
# completion_report.yaml
# ---------------------------------------------------------------------------


@dataclass
class CompletionItem:
    id: str
    completed: bool
    location: str | None = None
    blocking_reason: str | None = None
    note: str | None = None


@dataclass
class CompletionReport:
    iteration: int
    attempt: int
    items: list[CompletionItem]
    unimplementable_items: list[str]

    def is_unimplementable(self) -> bool:
        """True if subagent reported any item as unimplementable (route to Stage C)."""
        return bool(self.unimplementable_items)

    def failed_items(self) -> list[str]:
        """Item ids whose ``completed`` is False (regardless of unimplementable_items)."""
        return [it.id for it in self.items if not it.completed]


def _parse_completion_item(raw: dict, idx: int, unimplementable_set: set[str]) -> CompletionItem:
    ctx = f"completion_report.items[{idx}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{ctx}: must be a mapping")
    item_id = _require_type(_require(raw, "id", ctx), str, ctx, "id")
    completed = _require_type(_require(raw, "completed", ctx), bool, ctx, "completed")
    location = raw.get("location")
    blocking_reason = raw.get("blocking_reason")
    note = raw.get("note")

    if completed:
        if not isinstance(location, str) or not location:
            raise ValidationError(
                f"{ctx}: 'location' (string) required when completed=true"
            )
    else:
        # completed = false
        if item_id in unimplementable_set:
            if not isinstance(blocking_reason, str) or not blocking_reason:
                raise ValidationError(
                    f"{ctx}: 'blocking_reason' required when item is in unimplementable_items"
                )
    return CompletionItem(
        id=item_id, completed=completed, location=location,
        blocking_reason=blocking_reason, note=note,
    )


def parse_completion_report(yaml_str: str) -> CompletionReport:
    """Parse and validate completion_report.yaml. Raises ValidationError."""
    try:
        raw = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValidationError(f"completion_report.yaml: invalid YAML: {e}")
    if not isinstance(raw, dict):
        raise ValidationError("completion_report.yaml: top-level must be a mapping")

    ctx = "completion_report"
    iteration = _require_type(_require(raw, "iteration", ctx), int, ctx, "iteration")
    attempt = _require_type(_require(raw, "attempt", ctx), int, ctx, "attempt")
    items_raw = _require(raw, "items", ctx)
    if not isinstance(items_raw, list):
        raise ValidationError("completion_report.items: must be a list")

    unimplementable_raw = _require(raw, "unimplementable_items", ctx)
    if not isinstance(unimplementable_raw, list) or not all(
        isinstance(v, str) for v in unimplementable_raw
    ):
        raise ValidationError("completion_report.unimplementable_items: must be a list of strings")
    unimplementable_set = set(unimplementable_raw)

    items = [_parse_completion_item(it, idx, unimplementable_set) for idx, it in enumerate(items_raw)]

    # consistency: every id in unimplementable_items must appear in items as completed=false
    item_index = {it.id: it for it in items}
    for uid in unimplementable_raw:
        if uid not in item_index:
            raise ValidationError(
                f"completion_report.unimplementable_items: id '{uid}' not present in items"
            )
        if item_index[uid].completed:
            raise ValidationError(
                f"completion_report.unimplementable_items: '{uid}' has completed=true; "
                f"unimplementable items must have completed=false"
            )

    return CompletionReport(
        iteration=iteration,
        attempt=attempt,
        items=items,
        unimplementable_items=list(unimplementable_raw),
    )


# ---------------------------------------------------------------------------
# audit_report.yaml
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT = "INSUFFICIENT"


class Overall(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


CHECKLIST_AMBIGUOUS_TAG = "CHECKLIST_AMBIGUOUS"
UNAUTHORIZED_CHANGE_ID = "unauthorized_change"


@dataclass
class CheckResult:
    """Single audit check result; covers deterministic and LLM3 layers."""

    id: str
    verdict: Verdict
    evidence: str
    # tri-state: True / False / None.
    # None means "not applicable to dishonest detection" (invariant or
    # unauthorized_change special entry — no corresponding checklist item id).
    subagent_self_report_consistent: bool | None = None
    suggested_fix: str | None = None  # populated by LLM3 on FAIL


@dataclass
class AuditReport:
    iteration: int
    attempt: int
    deterministic_results: list[CheckResult]
    llm3_results: list[CheckResult]
    overall: Overall
    reject_summary: str | None = None

    def should_route_to_stage_c(self) -> bool:
        """True iff this audit's REJECTED reason indicates the checklist itself is the problem.

        Two cases:
        - reject_summary contains CHECKLIST_AMBIGUOUS (LLM3 returned INSUFFICIENT)
        - the orchestrator should pair this with a Stage C re-generation,
          NOT a Stage D rewrite (subagent_retry should not increment).
        """
        if self.overall != Overall.REJECTED:
            return False
        return self.reject_summary is not None and CHECKLIST_AMBIGUOUS_TAG in self.reject_summary

    def has_dishonest_attempt(self) -> bool:
        """True iff any result (deterministic or LLM3) reports inconsistent self-report.

        Used by the orchestrator's dishonest-counter logic; an attempt is a
        ``dishonest_attempt`` if at least one False appears across both layers.
        ``None`` values (invariant / unauthorized_change) do not contribute.
        """
        for r in self.deterministic_results + self.llm3_results:
            if r.subagent_self_report_consistent is False:
                return True
        return False


def _parse_check_result(raw: dict, ctx: str) -> CheckResult:
    if not isinstance(raw, dict):
        raise ValidationError(f"{ctx}: must be a mapping")
    rid = _require_type(_require(raw, "id", ctx), str, ctx, "id")
    verdict_raw = _require(raw, "verdict", ctx)
    try:
        verdict = Verdict(verdict_raw)
    except ValueError:
        raise ValidationError(
            f"{ctx}.verdict: '{verdict_raw}' must be one of {[v.value for v in Verdict]}"
        )
    evidence = _require_type(_require(raw, "evidence", ctx), str, ctx, "evidence")
    if "subagent_self_report_consistent" not in raw:
        raise ValidationError(
            f"{ctx}: 'subagent_self_report_consistent' is required (use null when N/A)"
        )
    consistent = raw["subagent_self_report_consistent"]
    if consistent is not None and not isinstance(consistent, bool):
        raise ValidationError(
            f"{ctx}.subagent_self_report_consistent: must be bool or null"
        )
    suggested_fix = raw.get("suggested_fix")
    if suggested_fix is not None and not isinstance(suggested_fix, str):
        raise ValidationError(f"{ctx}.suggested_fix: must be string or absent")
    return CheckResult(
        id=rid, verdict=verdict, evidence=evidence,
        subagent_self_report_consistent=consistent, suggested_fix=suggested_fix,
    )


def parse_audit_report(yaml_str: str) -> AuditReport:
    """Parse and validate audit_report.yaml. Raises ValidationError."""
    try:
        raw = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValidationError(f"audit_report.yaml: invalid YAML: {e}")
    if not isinstance(raw, dict):
        raise ValidationError("audit_report.yaml: top-level must be a mapping")

    iteration = _require_type(_require(raw, "iteration", "audit_report"), int, "audit_report", "iteration")
    attempt = _require_type(_require(raw, "attempt", "audit_report"), int, "audit_report", "attempt")
    det_raw = _require(raw, "deterministic_results", "audit_report")
    if not isinstance(det_raw, list):
        raise ValidationError("audit_report.deterministic_results: must be a list")
    llm3_raw = _require(raw, "llm3_results", "audit_report")
    if not isinstance(llm3_raw, list):
        raise ValidationError("audit_report.llm3_results: must be a list")

    overall_raw = _require(raw, "overall", "audit_report")
    try:
        overall = Overall(overall_raw)
    except ValueError:
        raise ValidationError(
            f"audit_report.overall: '{overall_raw}' must be APPROVED|REJECTED"
        )
    reject_summary = raw.get("reject_summary")
    if overall == Overall.REJECTED:
        if not isinstance(reject_summary, str) or not reject_summary:
            raise ValidationError("audit_report.reject_summary: required when overall=REJECTED")
    if overall == Overall.APPROVED and reject_summary:
        raise ValidationError(
            "audit_report.reject_summary: must be empty when overall=APPROVED"
        )

    deterministic_results = [
        _parse_check_result(r, f"audit_report.deterministic_results[{i}]")
        for i, r in enumerate(det_raw)
    ]
    llm3_results = [
        _parse_check_result(r, f"audit_report.llm3_results[{i}]")
        for i, r in enumerate(llm3_raw)
    ]

    # Spec rule: deterministic FAIL → llm3_results SHALL be empty (short-circuit).
    if any(r.verdict == Verdict.FAIL for r in deterministic_results) and llm3_results:
        raise ValidationError(
            "audit_report.llm3_results: must be empty when any deterministic result is FAIL"
        )
    # Spec rule: INSUFFICIENT only valid in llm3_results.
    for r in deterministic_results:
        if r.verdict == Verdict.INSUFFICIENT:
            raise ValidationError(
                f"audit_report.deterministic_results[{r.id}]: "
                f"INSUFFICIENT verdict only valid in llm3_results"
            )

    return AuditReport(
        iteration=iteration,
        attempt=attempt,
        deterministic_results=deterministic_results,
        llm3_results=llm3_results,
        overall=overall,
        reject_summary=reject_summary,
    )


# ---------------------------------------------------------------------------
# cross_check: routing decision (Stage D output → orchestrator)
# ---------------------------------------------------------------------------


class RoutingDecision(str, Enum):
    UNIMPLEMENTABLE_CHECKLIST = "UNIMPLEMENTABLE_CHECKLIST"  # → Stage C
    IMPLEMENTATION_FAILED = "IMPLEMENTATION_FAILED"          # → Stage D
    READY_FOR_AUDIT = "READY_FOR_AUDIT"                      # → Stage E


def cross_check(checklist: Checklist, report: CompletionReport) -> RoutingDecision:
    """Decide the next route based on a completion_report.

    Per the spec contract rules in revise-checklist-protocol:

    1. ``unimplementable_items`` non-empty → route to Stage C
    2. ``unimplementable_items`` empty, all completed → route to Stage E
    3. ``unimplementable_items`` empty, any completed=false → route to Stage D

    The checklist itself is not currently inspected (signature reserved for
    future use such as id-set sanity checking).
    """
    # contract rule 1
    if report.is_unimplementable():
        return RoutingDecision.UNIMPLEMENTABLE_CHECKLIST
    # contract rule 3 takes priority over 2 because "any false" is the failure case
    if any(not it.completed for it in report.items):
        return RoutingDecision.IMPLEMENTATION_FAILED
    # contract rule 2
    # sanity: every checklist item should have a corresponding report item
    checklist_ids = {it.id for it in checklist.items}
    report_ids = {it.id for it in report.items}
    if checklist_ids - report_ids:
        # subagent dropped items; treat as Stage D failure
        return RoutingDecision.IMPLEMENTATION_FAILED
    return RoutingDecision.READY_FOR_AUDIT
