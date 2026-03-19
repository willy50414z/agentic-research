"""
framework/tag_parser.py

Parses structured tags from CLI agent stdout to extract routing signals.
Avoids JSON hallucination issues by using plain-text XML-style tags.

Expected stdout format from Claude CLI:
    <RESULT>PASS</RESULT>
    <REASON>Win rate exceeded threshold</REASON>
    <CONTENT>...main output...</CONTENT>
"""

import re
import subprocess
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    result: str          # PASS | FAIL | TERMINATE | DRAFT | UNKNOWN
    reason: str
    content: str         # main output (code, report, plan, etc.)
    raw_stdout: str
    returncode: int = 0
    stderr: str = ""


def call_cli_agent(cmd: list[str], prompt: str, timeout: int = 300) -> CLIResult:
    """
    Invokes a local CLI agent (e.g. Claude CLI) and parses structured tags from stdout.

    Args:
        cmd: Base command, e.g. ["claude", "--model", "claude-opus-4-6", "--print"]
        prompt: The prompt to append as the final argument.
        timeout: Seconds before the subprocess is killed.

    Returns:
        CLIResult with parsed fields.

    Example usage in analyze_node:
        result = call_cli_agent(["claude", "--print"], analyze_prompt)
        if result.result == "PASS":
            return {"last_result": "PASS", "last_reason": result.reason}
    """
    full_cmd = cmd + [prompt]
    logger.debug("Calling CLI agent: %s", " ".join(full_cmd[:3]) + " ...")

    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("CLI agent timed out after %ds", timeout)
        return CLIResult(
            result="FAIL",
            reason=f"CLI agent timed out after {timeout}s",
            content="",
            raw_stdout="",
            returncode=-1,
        )
    except FileNotFoundError as e:
        logger.error("CLI agent binary not found: %s", e)
        return CLIResult(
            result="FAIL",
            reason=f"CLI binary not found: {e}",
            content="",
            raw_stdout="",
            returncode=-1,
        )

    stdout = proc.stdout
    stderr = proc.stderr

    if proc.returncode != 0:
        logger.warning("CLI agent exited with code %d. stderr: %s", proc.returncode, stderr[:200])

    return CLIResult(
        result=_extract_tag(stdout, "RESULT") or "UNKNOWN",
        reason=_extract_tag(stdout, "REASON") or "",
        content=_extract_tag(stdout, "CONTENT") or stdout,
        raw_stdout=stdout,
        returncode=proc.returncode,
        stderr=stderr,
    )


def _extract_tag(text: str, tag: str) -> str | None:
    """Extracts content between <TAG>...</TAG>. Returns None if not found."""
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else None
