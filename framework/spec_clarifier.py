"""
framework/spec_clarifier.py

Dual-LLM spec-writing agent for the Planka-first workflow.

Flow:
  1. Primary agent downloads spec.md, rewrites it with full domain detail, uploads new version.
  2. Secondary agent reviews for executability/consistency, uploads final version.
  3. Framework parses final spec.md with regex to extract structured fields.

LLM output format: pure markdown spec.md + <!-- AGENT_META ... --> block at the end.
Framework parses metadata from the HTML comment block — LLM never outputs JSON.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SpecAgentResult:
    needs_user_input: bool
    questions: list[str]
    enhanced_spec_md: str       # LLM-rewritten spec.md (metadata block stripped)
    domain: str
    agent_notes: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_spec_agent(
    spec_md: str,
    llm_fn: Callable[[str], str] | None,
    role: str = "primary",
) -> SpecAgentResult:
    """
    Run a spec-writing agent (primary or secondary) against spec_md.

    Args:
        spec_md:  Current spec.md content (raw markdown string).
        llm_fn:   Callable(prompt: str) -> str.  If None, returns conservative fallback.
        role:     "primary" or "secondary" — selects prompt template.

    Returns:
        SpecAgentResult with enhanced_spec_md (metadata block stripped) and parsed metadata.
    """
    if llm_fn is None:
        logger.warning("run_spec_agent: no LLM available — returning spec unchanged.")
        return SpecAgentResult(
            needs_user_input=True,
            questions=["No LLM provider configured. Please set LLM_CHAIN in environment."],
            enhanced_spec_md=spec_md,
            domain="unknown",
            agent_notes="No LLM available.",
        )

    system_prompt = _load_prompt(role)
    full_prompt = f"{system_prompt}\n\n---\n\n## Input spec.md\n\n{spec_md}"

    try:
        response = llm_fn(full_prompt)
    except Exception as e:
        logger.warning("run_spec_agent LLM call failed (%s): %s", role, e)
        return SpecAgentResult(
            needs_user_input=True,
            questions=[f"LLM call failed: {e}"],
            enhanced_spec_md=spec_md,
            domain="unknown",
            agent_notes=f"LLM error: {e}",
        )

    return _parse_agent_response(response, original_spec=spec_md)


def parse_spec_md(spec_md: str) -> dict:
    """
    Extract structured fields from a completed spec.md.

    Returns a dict suitable for storing in projects.config["spec"] and
    passing as ResearchState["spec"].

    Parsed fields:
      plugin, hypothesis, domain,
      performance: {win_rate, max_drawdown, alpha_ratio, is_profit_factor, oos_profit_factor},
      universe: {instruments, exchange, timeframe, train_start, train_end, test_start, test_end},
      entry_signal, exit_signal, notes
    """
    def _section(header: str) -> str:
        pattern = rf"## {re.escape(header)}\s*\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, spec_md, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _float(text: str, patterns: list[str]) -> float | None:
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
        return None

    # Plugin
    plugin_section = _section("Plugin")
    plugin_m = re.search(r"(\w+)", plugin_section)
    plugin = plugin_m.group(1).strip() if plugin_m else "quant_alpha"

    # Domain
    domain = _section("Domain").strip().splitlines()[0] if _section("Domain") else "unknown"

    # Hypothesis
    hypothesis = _section("Hypothesis") or _section("Research Goal")

    # Performance thresholds
    perf_text = _section("Performance Thresholds")
    win_rate = _float(perf_text, [
        r"win.?rate[:\s]+([0-9.]+)",
        r"min.?win[:\s]+([0-9.]+)",
    ])
    max_drawdown = _float(perf_text, [
        r"max.?drawdown[:\s]+([0-9.]+)",
        r"drawdown[:\s]+([0-9.]+)",
    ])
    alpha_ratio = _float(perf_text, [
        r"alpha.?ratio[:\s]+([0-9.]+)",
        r"min.?alpha[:\s]+([0-9.]+)",
    ])
    is_pf = _float(perf_text, [
        r"in.?sample.?profit.?factor[:\s]+([0-9.]+)",
        r"min.{0,20}profit.?factor[:\s]+([0-9.]+)",
    ])
    oos_pf = _float(perf_text, [
        r"out.?of.?sample.?profit.?factor[:\s]+([0-9.]+)",
        r"oos.?profit.?factor[:\s]+([0-9.]+)",
    ])

    # Universe
    universe_text = _section("Universe")
    instruments = re.findall(r"Instruments?[:\s]+([^\n]+)", universe_text, re.IGNORECASE)
    exchange = re.findall(r"Exchange[:\s]+([^\n]+)", universe_text, re.IGNORECASE)
    timeframe = re.findall(r"Timeframe[:\s]+([^\n]+)", universe_text, re.IGNORECASE)
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", universe_text)

    # Signals
    entry_signal = _section("Entry Signal")
    exit_signal = _section("Exit Signal")

    # Agent notes
    agent_notes = _section("Agent Notes")

    return {
        "plugin": plugin,
        "domain": domain,
        "hypothesis": hypothesis,
        "performance": {
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "alpha_ratio": alpha_ratio,
            "is_profit_factor": is_pf,
            "oos_profit_factor": oos_pf,
        },
        "universe": {
            "instruments": instruments[0].strip() if instruments else "",
            "exchange": exchange[0].strip() if exchange else "",
            "timeframe": timeframe[0].strip() if timeframe else "",
            "train_start": dates[0] if len(dates) > 0 else "",
            "train_end": dates[1] if len(dates) > 1 else "",
            "test_start": dates[2] if len(dates) > 2 else "",
            "test_end": dates[3] if len(dates) > 3 else "",
        },
        "entry_signal": entry_signal,
        "exit_signal": exit_signal,
        "agent_notes": agent_notes,
        "raw_md": spec_md,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_prompt(role: str) -> str:
    """Load system prompt from framework/prompts/ directory."""
    prompt_file = Path(__file__).parent / "prompts" / f"spec_agent_{role}.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    # Fallback inline prompt
    logger.warning("Prompt file not found: %s — using inline fallback.", prompt_file)
    return (
        "You are a research specification agent. "
        "Rewrite the given spec.md to be complete and executable. "
        "End your response with:\n"
        "<!-- AGENT_META\n"
        "needs_user_input: false\n"
        "domain: <domain>\n"
        "questions: []\n"
        "agent_notes: <what you did>\n"
        "-->"
    )


def _parse_agent_response(response: str, original_spec: str) -> SpecAgentResult:
    """
    Parse LLM response:
      1. Extract <!-- AGENT_META ... --> block for metadata.
      2. Strip the block from the spec markdown.

    If the block is missing or malformed, treat conservatively as needs_user_input=True.
    """
    meta_pattern = re.compile(
        r"<!--\s*AGENT_META\s*(.*?)\s*-->",
        re.DOTALL,
    )
    meta_match = meta_pattern.search(response)

    if not meta_match:
        logger.warning("AGENT_META block not found in LLM response — treating as needs_user_input.")
        return SpecAgentResult(
            needs_user_input=True,
            questions=["Agent response did not include required AGENT_META block."],
            enhanced_spec_md=response.strip() or original_spec,
            domain="unknown",
            agent_notes="Missing AGENT_META block.",
        )

    meta_text = meta_match.group(1)
    spec_md = meta_pattern.sub("", response).strip()

    # Parse metadata fields
    needs_input = _parse_bool(meta_text, "needs_user_input", default=True)
    domain = _parse_str(meta_text, "domain", default="unknown")
    agent_notes = _parse_str(meta_text, "agent_notes", default="")
    questions = _parse_list(meta_text, "questions")

    return SpecAgentResult(
        needs_user_input=needs_input,
        questions=questions,
        enhanced_spec_md=spec_md or original_spec,
        domain=domain,
        agent_notes=agent_notes,
    )


def _parse_bool(text: str, key: str, default: bool = False) -> bool:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return default
    return m.group(1).strip().lower() in ("true", "yes", "1")


def _parse_str(text: str, key: str, default: str = "") -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return default
    return m.group(1).strip()


def _parse_list(text: str, key: str) -> list[str]:
    """Parse a YAML-style list or inline [] from metadata block."""
    # Inline empty: questions: []
    inline_m = re.search(rf"^{re.escape(key)}:\s*\[\s*\]", text, re.MULTILINE | re.IGNORECASE)
    if inline_m:
        return []
    # Multi-line list items:  - "item"
    list_m = re.search(
        rf"^{re.escape(key)}:\s*\n((?:\s+-\s+.+\n?)*)",
        text, re.MULTILINE | re.IGNORECASE,
    )
    if list_m:
        items = re.findall(r"^\s+-\s+[\"']?(.+?)[\"']?\s*$", list_m.group(1), re.MULTILINE)
        return [i.strip() for i in items if i.strip()]
    return []
