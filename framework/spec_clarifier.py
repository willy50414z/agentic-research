"""
framework/spec_clarifier.py

Phase 1 spec processing: markdown parsing + rule validation + LLM clarification.

User-facing files use .md (readable); internal processing uses dict (YAML-compatible).

Responsibilities:
  - load_spec_md(path)           → dict  (LLM converts .md → structured dict)
  - load_spec(path)              → dict  (load .yaml spec, for backward compat)
  - validate_spec(spec)          → raises SpecValidationError on missing required fields
  - generate_clarifications(spec, llm_fn) → list[dict]  (field, original, question, answer)
  - write_clarified_md(path, spec, clarifications)  → spec.clarified.md (markdown Q&A)
  - write_clarified_spec(path, spec, clarifications) → spec.clarified.yaml (legacy)
  - read_clarified_answers_md(path) → dict[field, answer]
  - read_clarified_answers(path) → dict[field, answer]
  - all_answered(clarifications) → bool
"""

import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SpecValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Required fields (dot-notation)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "project.label",
    "research.hypothesis",
    "research.universe.instruments",
    "research.data.source",
    "research.data.train",
    "research.performance",
    "research.plugin",
]


# ---------------------------------------------------------------------------
# Fuzzy fields that LLM should evaluate
# ---------------------------------------------------------------------------

_FUZZY_FIELDS = [
    "research.signals.entry",
    "research.signals.exit",
    "research.optimization.method",
    "research.notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nested(d: dict, dotpath: str):
    keys = dotpath.split(".")
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _dict_to_yaml_comment(d: dict, indent: int = 0) -> str:
    """Render a dict as commented-out YAML lines."""
    raw = yaml.dump(d, allow_unicode=True, default_flow_style=False, sort_keys=False)
    lines = raw.splitlines()
    return "\n".join(" " * indent + "# " + line for line in lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_spec_md(path: str | Path, llm_fn: Callable[[str], str] | None = None) -> dict:
    """
    Load a spec.md file and convert it to a structured dict.

    If llm_fn is provided, the LLM parses the markdown into structured YAML.
    Otherwise, falls back to a best-effort regex extraction.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"spec.md not found: {p}")
    md_content = p.read_text(encoding="utf-8")

    if llm_fn is not None:
        return _md_to_spec_via_llm(md_content, llm_fn)
    return _md_to_spec_regex(md_content)


def _md_to_spec_via_llm(md_content: str, llm_fn: Callable[[str], str]) -> dict:
    """Use LLM to convert markdown spec to structured YAML dict."""
    prompt = (
        "Convert the following research spec (in Markdown) into a structured YAML dict "
        "with these top-level keys: project (label, name), research (hypothesis, universe, "
        "data, signals, optimization, performance, tools, plugin, review_interval, max_loops, notes).\n\n"
        "Output ONLY valid YAML, no code fences, no explanation.\n\n"
        f"--- spec.md ---\n{md_content}\n---"
    )
    try:
        response = llm_fn(prompt).strip()
        # Strip code fences if LLM added them
        if response.startswith("```"):
            lines = response.splitlines()
            response = "\n".join(
                ln for ln in lines
                if not ln.startswith("```")
            )
        data = yaml.safe_load(response)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _md_to_spec_regex(md_content)


def _md_to_spec_regex(md_content: str) -> dict:
    """
    Best-effort regex extraction from markdown spec when LLM is unavailable.
    Extracts project label from h1, hypothesis from ## Hypothesis section, etc.
    """
    import re

    def _section(header: str) -> str:
        pattern = rf"## {re.escape(header)}\s*\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, md_content, re.DOTALL)
        return m.group(1).strip() if m else ""

    # Project label from # heading
    title_match = re.search(r"^# Research Spec:\s*(.+)$", md_content, re.MULTILINE)
    project_name = title_match.group(1).strip() if title_match else "unknown"
    project_label = re.sub(r"[^a-z0-9-]", "-", project_name.lower()).strip("-")

    hypothesis = _section("Hypothesis")

    # Parse instruments list
    universe_text = _section("Universe")
    instruments = re.findall(r"\b([A-Z]{1,5})\b", universe_text)

    # Parse data dates
    data_text = _section("Data")
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", data_text)
    train = dates[:2] if len(dates) >= 2 else ["2018-01-01", "2022-12-31"]
    test = dates[2:4] if len(dates) >= 4 else ["2023-01-01", "2024-12-31"]

    # Signals
    signals_text = _section("Signals")
    entry_m = re.search(r"\*\*Entry\*\*:\s*(.+)", signals_text)
    exit_m = re.search(r"\*\*Exit\*\*:\s*(.+)", signals_text)

    # Settings
    settings_text = _section("Settings")
    plugin_m = re.search(r"\*\*Plugin\*\*:\s*(\S+)", settings_text)
    review_m = re.search(r"Review every\*\*:\s*(\d+)", settings_text)
    max_m = re.search(r"Max loops\*\*:\s*(\d+)", settings_text)

    return {
        "project": {"label": project_label, "name": project_name},
        "research": {
            "hypothesis": hypothesis,
            "universe": {
                "instruments": instruments or ["AAPL"],
                "asset_class": "equity",
                "frequency": "daily",
            },
            "data": {"source": "yfinance", "train": train, "test": test},
            "signals": {
                "entry": entry_m.group(1).strip() if entry_m else "",
                "exit": exit_m.group(1).strip() if exit_m else "",
            },
            "performance": {
                "sharpe_ratio": {"min": 1.0},
                "max_drawdown": {"max": 0.20},
                "profit_factor": {"min": 1.2},
                "win_rate": {"min": 0.45},
                "oos_profit_factor": {"min": 1.1},
            },
            "plugin": plugin_m.group(1) if plugin_m else "quant_strategy",
            "review_interval": int(review_m.group(1)) if review_m else 5,
            "max_loops": int(max_m.group(1)) if max_m else 30,
            "notes": _section("Notes"),
        },
    }


def load_spec(path: str | Path) -> dict:
    """Load and parse spec.yaml from disk."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"spec.yaml not found: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SpecValidationError("spec.yaml must be a YAML mapping.")
    return data


def validate_spec(spec: dict) -> None:
    """
    Rule-based validation: check required fields exist and are non-empty.
    Raises SpecValidationError listing all missing fields.
    """
    missing = []
    for field in _REQUIRED_FIELDS:
        value = _get_nested(spec, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    if missing:
        raise SpecValidationError(
            "Missing required fields in spec.yaml:\n" +
            "\n".join(f"  - {f}" for f in missing)
        )


def generate_clarifications(
    spec: dict,
    llm_fn: Callable[[str], str] | None = None,
) -> list[dict]:
    """
    Generate clarification questions for fuzzy fields.

    llm_fn: callable that takes a prompt string and returns the LLM response.
            If None, uses rule-based placeholder questions.

    Returns a list of:
        {"field": str, "original": str, "question": str, "answer": ""}
    """
    clarifications = []

    for dotpath in _FUZZY_FIELDS:
        value = _get_nested(spec, dotpath)
        if value is None:
            continue  # skip missing optional fields

        str_value = str(value).strip()
        if not str_value:
            continue

        if llm_fn is not None:
            prompt = _build_clarification_prompt(dotpath, str_value, spec)
            try:
                response = llm_fn(prompt)
                question = _extract_question(response)
            except Exception:
                question = _default_question(dotpath, str_value)
        else:
            question = _default_question(dotpath, str_value)

        if question:
            clarifications.append({
                "field": dotpath,
                "original": str_value,
                "question": question,
                "answer": "",
            })

    return clarifications


def write_clarified_spec(
    path: str | Path,
    spec: dict,
    clarifications: list[dict],
) -> None:
    """
    Write spec.clarified.yaml with:
    - Header comment block containing the original spec as a snapshot
    - clarifications list with questions and empty answer slots
    """
    path = Path(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    snapshot_comment = _dict_to_yaml_comment(spec)

    clarifications_yaml = yaml.dump(
        {"clarifications": clarifications},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )

    content = textwrap.dedent(f"""\
        # ============================================================
        # ORIGINAL SPEC SNAPSHOT（captured: {timestamp}）
        # 此快照在 start.sh 時自動產生，原始 spec.yaml 不受影響。
        # ============================================================
        {snapshot_comment}
        # ============================================================

        {clarifications_yaml}""")

    path.write_text(content, encoding="utf-8")


def read_clarified_answers(path: str | Path) -> dict[str, str]:
    """
    Parse spec.clarified.yaml and return {field: answer} for all entries.
    Strips comment lines before parsing.
    """
    path = Path(path)
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    # Strip comment lines so PyYAML can parse the document cleanly
    lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
    cleaned = "\n".join(lines)

    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        return {}

    if not isinstance(data, dict):
        return {}

    result = {}
    for item in data.get("clarifications") or []:
        if isinstance(item, dict):
            field = item.get("field", "")
            answer = item.get("answer", "") or ""
            result[field] = answer.strip()
    return result


def all_answered(clarifications: list[dict]) -> bool:
    """Return True if every clarification has a non-empty answer."""
    return all(bool((c.get("answer") or "").strip()) for c in clarifications)


def write_clarified_md(
    path: str | Path,
    spec: dict,
    clarifications: list[dict],
) -> None:
    """
    Write spec.clarified.md — a user-facing markdown Q&A file.

    Format:
      - Header with snapshot timestamp
      - One section per clarification with field, original value, question, answer slot
    """
    from datetime import datetime, timezone
    path = Path(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    project = spec.get("project", {})
    research = spec.get("research", {})

    lines = [
        f"# Spec Clarifications",
        f"",
        f"**Project**: {project.get('label', 'unknown')}  ",
        f"**Generated**: {timestamp}",
        f"",
        f"> Fill in each **Answer** below, then run `./resume.sh`.",
        f"",
        f"---",
        f"",
    ]

    if not clarifications:
        lines += [
            "## No Clarifications Needed",
            "",
            "All fields are clear. Run `./resume.sh` to start the research loop.",
            "",
        ]
    else:
        for i, c in enumerate(clarifications, 1):
            lines += [
                f"## {i}. `{c['field']}`",
                f"",
                f"**Original value**  ",
                f"```",
                f"{c['original']}",
                f"```",
                f"",
                f"**Question**  ",
                f"{c['question']}",
                f"",
                f"**Answer**  ",
                f"<!-- fill in below -->",
                f"",
                f"",
                f"---",
                f"",
            ]

    # Append original spec as a collapsed reference
    spec_yaml = yaml.dump(spec, allow_unicode=True, default_flow_style=False, sort_keys=False)
    lines += [
        "<details>",
        "<summary>Original spec snapshot</summary>",
        "",
        "```yaml",
        spec_yaml.rstrip(),
        "```",
        "</details>",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def read_clarified_answers_md(path: str | Path) -> dict[str, str]:
    """
    Parse answers from spec.clarified.md.

    Looks for sections of the form:
      ## N. `field.name`
      ...
      **Answer**
      <!-- fill in below -->
      <actual answer text>
    """
    import re
    path = Path(path)
    if not path.exists():
        return {}

    content = path.read_text(encoding="utf-8")
    answers: dict[str, str] = {}

    # Find each section
    section_pattern = re.compile(
        r"## \d+\.\s+`([^`]+)`.*?\*\*Answer\*\*\s*\n"
        r"<!-- fill in below -->\s*\n"
        r"(.*?)(?=\n---|\n## |\Z)",
        re.DOTALL,
    )
    for m in section_pattern.finditer(content):
        field = m.group(1).strip()
        answer = m.group(2).strip()
        answers[field] = answer

    return answers


def load_clarifications_md(path: str | Path) -> list[dict]:
    """
    Load clarifications from spec.clarified.md.
    Returns list of {field, original, question, answer}.
    """
    import re
    path = Path(path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    clarifications = []

    section_pattern = re.compile(
        r"## \d+\.\s+`([^`]+)`\s*\n"
        r".*?\*\*Original value\*\*\s*\n```\n(.*?)```\s*\n"
        r".*?\*\*Question\*\*\s*\n(.*?)\n"
        r".*?\*\*Answer\*\*\s*\n<!-- fill in below -->\s*\n"
        r"(.*?)(?=\n---|\n## |\Z)",
        re.DOTALL,
    )
    for m in section_pattern.finditer(content):
        clarifications.append({
            "field": m.group(1).strip(),
            "original": m.group(2).strip(),
            "question": m.group(3).strip(),
            "answer": m.group(4).strip(),
        })
    return clarifications


def load_clarifications(path: str | Path) -> list[dict]:
    """Load the clarifications list from spec.clarified.yaml."""
    path = Path(path)
    if not path.exists():
        return []

    raw = path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
    cleaned = "\n".join(lines)

    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        return []

    if not isinstance(data, dict):
        return []
    return data.get("clarifications") or []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_clarification_prompt(dotpath: str, value: str, spec: dict) -> str:
    hypothesis = _get_nested(spec, "research.hypothesis") or ""
    return (
        f"You are reviewing a quantitative research spec.\n\n"
        f"Field: {dotpath}\n"
        f"Current value: {value!r}\n"
        f"Research hypothesis: {hypothesis!r}\n\n"
        "If this field is ambiguous or underspecified, generate ONE concise clarifying "
        "question in Traditional Chinese (繁體中文). "
        "If the field is already clear and unambiguous, respond with: CLEAR\n\n"
        "Response format: just the question or 'CLEAR'."
    )


def _extract_question(response: str) -> str:
    """Return empty string if response is CLEAR, else return the question."""
    stripped = response.strip()
    if stripped.upper() == "CLEAR" or stripped.upper().startswith("CLEAR"):
        return ""
    return stripped


def _default_question(dotpath: str, value: str) -> str:
    """Fallback rule-based questions when LLM is unavailable."""
    defaults = {
        "research.signals.entry": (
            f"入場信號 {value!r} 的交叉確認方式？"
            "（1）單根 K 棒收盤 （2）N 根連續收盤"
        ),
        "research.signals.exit": (
            f"出場信號 {value!r} 的確認方式？同上還是不同？"
        ),
        "research.optimization.method": (
            f"優化方式 {value!r} 的停止條件？"
            "（1）固定迭代次數 （2）收斂閾值"
        ),
        "research.notes": "",  # notes are optional hints, no question needed
    }
    return defaults.get(dotpath, "")
