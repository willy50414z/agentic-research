"""
framework/spec_clarifier.py

Dual-LLM spec-writing agent for the Planka-first workflow.

Flow:
  1. Primary agent reads spec.md and .ai/rules/spec-review.md from cwd, writes
     reviewed_spec.md and a status file (status_pass.txt or status_need_update.txt).
  2. Secondary agent repeats the same protocol on the primary's output.
  3. Framework detects outcome by checking which status file exists in work_dir.
  4. Framework parses final reviewed_spec.md with regex to extract structured fields.

Status detection:
  status_pass.txt present      → needs_user_input = False
  status_need_update.txt present → needs_user_input = True; questions from file lines
  neither present              → fallback to _parse_agent_response on stdout text
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
logger.warning("spec_clarifier v2 loaded from %s", __file__)


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
    spec_path: str,
    llm_fn: Callable[[str], str] | None,
    role: str = "initial",
    provider_name: str = "",
    round_index: int = 0,
) -> SpecAgentResult:
    """
    Run a spec-writing agent (primary or secondary) against a local spec.md file.

    Args:
        spec_path: Local path to the spec.md file.
        llm_fn:    Callable(prompt: str) -> str.  If None, returns conservative fallback.
        role:      "primary" or "secondary" — selects prompt template.

    Returns:
        SpecAgentResult with enhanced_spec_md (metadata block stripped) and parsed metadata.
    """
    original_spec = _read_spec_file(spec_path)
    work_dir = str(Path(spec_path).parent)

    if llm_fn is None:
        logger.warning("run_spec_agent: no LLM available — returning spec unchanged.")
        return SpecAgentResult(
            needs_user_input=True,
            questions=["No LLM provider configured. Please set LLM_CHAIN in environment."],
            enhanced_spec_md=original_spec,
            domain="unknown",
            agent_notes="No LLM available.",
        )

    # Clean up stale status files before each run so results are unambiguous.
    for stale in ("status_pass.txt", "status_need_update.txt"):
        Path(work_dir, stale).unlink(missing_ok=True)

    rules_path  = str((Path(__file__).parent.parent / ".ai" / "rules" / "spec-review.md").resolve())
    plugin_dir  = str((Path(__file__).parent.parent / "projects" / "quant_alpha").resolve())

    # Build review_notes block for synthesize role
    review_notes_block = ""
    if role == "synthesize":
        notes_files = sorted(Path(work_dir).glob("review_notes_round*.txt"))
        parts = []
        for nf in notes_files:
            parts.append(f"=== {nf.name} ===\n{nf.read_text(encoding='utf-8').strip()}")
        review_notes_block = "\n\n".join(parts) if parts else "(no reviewer notes)"

    prompt_template = _load_prompt(role)
    prompt = (
        prompt_template
        .replace("{SPEC_PATH}",        spec_path)
        .replace("{OUTPUT_DIR}",       work_dir)
        .replace("{RULES_PATH}",       rules_path)
        .replace("{PLUGIN_DIR}",       plugin_dir)
        .replace("{ROUND_INDEX}",      str(round_index))
        .replace("{REVIEW_NOTES}",     review_notes_block)
    )

    # Gemini requires strict output constraints prepended to every prompt
    if "gemini" in provider_name.lower():
        prompt = _gemini_prefix(role, work_dir, round_index) + prompt

    logger.info(
        "run_spec_agent [%s] spec_path=%s work_dir=%s\n--- prompt ---\n%s\n--- end prompt ---",
        role, spec_path, work_dir, prompt,
    )

    try:
        response = llm_fn(prompt, cwd=work_dir)
        # Persist LLM stdout response so it can be uploaded to the card later.
        llm_out_path = Path(work_dir) / f"llm_response_{role}.txt"
        try:
            llm_out_path.write_text(response or "", encoding="utf-8")
        except Exception as _e:
            logger.warning("Could not save LLM response to '%s': %s", llm_out_path, _e)
    except Exception as e:
        logger.warning("run_spec_agent LLM call failed (%s): %s", role, e)
        return SpecAgentResult(
            needs_user_input=True,
            questions=[f"LLM call failed: {e}"],
            enhanced_spec_md=original_spec,
            domain="unknown",
            agent_notes=f"LLM error: {e}",
        )

    # --- review role: read review_notes file, return without spec update ---
    if role == "review":
        notes_filename = f"review_notes_round{round_index}.txt"
        notes_path = Path(work_dir) / notes_filename
        if notes_path.exists():
            notes_text = notes_path.read_text(encoding="utf-8").strip()
            questions = [line.strip() for line in notes_text.splitlines() if line.strip()]
        else:
            logger.warning("run_spec_agent [review] notes file '%s' not found.", notes_path)
            questions = ["Reviewer did not produce a notes file."]
        return SpecAgentResult(
            needs_user_input=False,
            questions=questions,
            enhanced_spec_md=original_spec,   # reviewer does not change the spec
            domain=_extract_domain_from_spec(original_spec),
            agent_notes=f"Round {round_index} review by {provider_name or 'unknown'}.",
        )

    # Read the role-specific reviewed spec written by the agent, if present.
    _role_output_map = {
        "primary":    "reviewed_spec_primary.md",    # legacy
        "secondary":  "reviewed_spec_secondary.md",  # legacy
        "initial":    "reviewed_spec_initial.md",
        "synthesize": "reviewed_spec_final.md",
    }
    output_filename = _role_output_map.get(role, f"reviewed_spec_{role}.md")
    reviewed_spec_path = Path(work_dir) / output_filename
    enhanced_spec_md = (
        reviewed_spec_path.read_text(encoding="utf-8")
        if reviewed_spec_path.exists()
        else original_spec
    )

    pass_file = Path(work_dir) / "status_pass.txt"
    need_update_file = Path(work_dir) / "status_need_update.txt"

    if pass_file.exists():
        logger.info("run_spec_agent [%s] status=PASS (status_pass.txt found in '%s')", role, work_dir)
        domain = _extract_domain_from_spec(enhanced_spec_md)
        agent_notes = _extract_section(enhanced_spec_md, "Agent Notes")
        return SpecAgentResult(
            needs_user_input=False,
            questions=[],
            enhanced_spec_md=enhanced_spec_md,
            domain=domain,
            agent_notes=agent_notes,
        )

    if need_update_file.exists():
        questions = [
            line.strip()
            for line in need_update_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        logger.info(
            "run_spec_agent [%s] status=NEED_UPDATE (status_need_update.txt found in '%s', %d questions)",
            role, work_dir, len(questions),
        )
        domain = _extract_domain_from_spec(enhanced_spec_md)
        agent_notes = _extract_section(enhanced_spec_md, "Agent Notes")
        return SpecAgentResult(
            needs_user_input=True,
            questions=questions,
            enhanced_spec_md=enhanced_spec_md,
            domain=domain,
            agent_notes=agent_notes,
        )

    # Neither status file was written — the agent did not follow the protocol.
    # Treat as an error: needs_user_input=True so the card goes back to Planning.
    missing_files = []
    if not reviewed_spec_path.exists():
        missing_files.append(reviewed_spec_path.name)
    missing_files += ["status_pass.txt", "status_need_update.txt"]
    logger.error(
        "run_spec_agent [%s] NO STATUS FILE in '%s'. "
        "Agent did not write status_pass.txt or status_need_update.txt. "
        "reviewed_spec exists=%s. Treating as needs_user_input.",
        role, work_dir, reviewed_spec_path.exists(),
    )
    return SpecAgentResult(
        needs_user_input=True,
        questions=[f"Agent [{role}] did not produce a status file (status_pass.txt / status_need_update.txt). "
                   f"reviewed_spec exists: {reviewed_spec_path.exists()}"],
        enhanced_spec_md=enhanced_spec_md,
        domain=_extract_domain_from_spec(enhanced_spec_md),
        agent_notes="Missing status file — protocol violation.",
    )


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

def _read_spec_file(spec_path: str) -> str:
    """Read spec file content; return empty string on failure."""
    try:
        return Path(spec_path).read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("_read_spec_file failed for '%s': %s", spec_path, e)
        return ""


def _load_prompt(role: str) -> str:
    """Load system prompt from framework/prompts/ directory.

    Role aliases:
      'initial'    → spec_agent_initial.txt   (falls back to spec_agent_primary.txt)
      'primary'    → spec_agent_primary.txt   (legacy)
      'secondary'  → spec_agent_secondary.txt (legacy)
      'review'     → spec_agent_review.txt
      'synthesize' → spec_agent_synthesize.txt
    """
    prompts_dir = Path(__file__).parent / "prompts"
    candidates = [prompts_dir / f"spec_agent_{role}.txt"]
    # Fallback alias: initial → primary
    if role == "initial":
        candidates.append(prompts_dir / "spec_agent_primary.txt")
    for prompt_file in candidates:
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")
    logger.warning("Prompt file not found for role '%s' — using inline fallback.", role)
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


def _gemini_prefix(role: str, work_dir: str, round_index: int) -> str:
    """Return Gemini-specific output constraint prefix for a given role."""
    if role in ("initial", "primary"):
        return (
            "STRICT RULE: write exactly two files and nothing else."
            f" File 1: reviewed_spec_{'initial' if role == 'initial' else 'primary'}.md"
            f" at the path stated in the prompt ({work_dir})."
            " File 2: EITHER status_pass.txt (content: PASS) if no clarification questions,"
            " OR status_need_update.txt (one question per line) if questions exist."
            " Do NOT create any other file, directory, script, or code."
            " Do NOT implement any software. Do NOT run shell commands."
            " Use write_file tool. Do NOT print file contents to stdout.\n\n"
        )
    if role == "review":
        return (
            "STRICT RULE: write exactly one file and nothing else."
            f" File: review_notes_round{round_index}.txt"
            f" at the path stated in the prompt ({work_dir})."
            " Each line must be one review comment or question (plain text, no markdown)."
            " Do NOT create any other file, directory, script, or code."
            " Do NOT implement any software. Do NOT run shell commands."
            " Use write_file tool. Do NOT print file contents to stdout.\n\n"
        )
    if role == "synthesize":
        return (
            "STRICT RULE: write exactly two files and nothing else."
            f" File 1: reviewed_spec_final.md at the path stated in the prompt ({work_dir})."
            " File 2: EITHER status_pass.txt (content: PASS) if no clarification questions,"
            " OR status_need_update.txt (one question per line) if questions exist."
            " Do NOT create any other file, directory, script, or code."
            " Do NOT implement any software. Do NOT run shell commands."
            " Use write_file tool. Do NOT print file contents to stdout.\n\n"
        )
    return ""


def _parse_agent_response(response: str, original_spec: str) -> SpecAgentResult:
    """
    Parse LLM response containing file blocks:

      === FILE: reviewed_spec.md ===
      <content>
      === END FILE ===

      === FILE: pass.txt ===        ← review passed
      ...
      === END FILE ===

      OR

      === FILE: need_update.txt === ← needs user input; questions listed inside
      - question 1
      - question 2
      === END FILE ===

    If the block structure is missing or malformed, treat conservatively as needs_user_input=True.
    """
    file_pattern = re.compile(
        r"=== FILE:\s*(\S+)\s*===\s*\n(.*?)\n=== END FILE ===",
        re.DOTALL,
    )
    files = {m.group(1).strip(): m.group(2).strip() for m in file_pattern.finditer(response)}

    if not files:
        logger.warning("No file blocks found in LLM response — treating as needs_user_input.")
        return SpecAgentResult(
            needs_user_input=True,
            questions=["Agent response did not include required file blocks."],
            enhanced_spec_md=response.strip() or original_spec,
            domain="unknown",
            agent_notes="Missing file blocks.",
        )

    spec_md = files.get("reviewed_spec.md", "") or original_spec

    if "need_update.txt" in files:
        questions = [
            line.lstrip("- ").strip()
            for line in files["need_update.txt"].splitlines()
            if line.strip().startswith("-")
        ]
        domain = _extract_domain_from_spec(spec_md)
        agent_notes = _extract_section(spec_md, "Agent Notes")
        return SpecAgentResult(
            needs_user_input=True,
            questions=questions,
            enhanced_spec_md=spec_md,
            domain=domain,
            agent_notes=agent_notes,
        )

    if "pass.txt" in files:
        domain = _extract_domain_from_spec(spec_md)
        agent_notes = _extract_section(spec_md, "Agent Notes")
        return SpecAgentResult(
            needs_user_input=False,
            questions=[],
            enhanced_spec_md=spec_md,
            domain=domain,
            agent_notes=agent_notes,
        )

    logger.warning("Neither pass.txt nor need_update.txt found in LLM response — treating as needs_user_input.")
    return SpecAgentResult(
        needs_user_input=True,
        questions=["Agent response did not include pass.txt or need_update.txt."],
        enhanced_spec_md=spec_md,
        domain="unknown",
        agent_notes="Missing status file.",
    )


def _extract_domain_from_spec(spec_md: str) -> str:
    """Extract domain from reviewed_spec.md."""
    m = re.search(r"##\s*研究領域\s*\n([^\n#]+)", spec_md)
    if m:
        return m.group(1).strip()
    m = re.search(r"##\s*Domain\s*\n([^\n#]+)", spec_md)
    if m:
        return m.group(1).strip()
    return "unknown"


def _extract_section(spec_md: str, header: str) -> str:
    """Extract a markdown section by header name."""
    m = re.search(rf"##\s*{re.escape(header)}\s*\n(.*?)(?=\n##|\Z)", spec_md, re.DOTALL)
    return m.group(1).strip() if m else ""


