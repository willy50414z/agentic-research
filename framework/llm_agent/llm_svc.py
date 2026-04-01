"""
framework/llm_agent/llm_svc.py

Single entry-point for calling any supported LLM CLI tool.

Adapted from sample/llm_agent/llm_svc.py — adds CLAUDE support.

Usage:
    from framework.llm_agent.llm_svc import run_once
    from framework.llm_agent.llm_target import LLMTarget

    raw_text = run_once(LLMTarget.CLAUDE, prompt)

Returns the raw stdout string. Callers are responsible for tag parsing
(use framework.tag_parser._extract_tag or CLIResult helpers).

Raises:
    RuntimeError  — CLI exited non-zero
    FileNotFoundError — CLI binary not on PATH (caller should catch and fallback)
    subprocess.TimeoutExpired — call exceeded timeout
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from framework.llm_agent.llm_target import LLMTarget

_OPENCODE_RUNTIME_DIR = Path("data") / "tool-runtime" / "opencode"
_REPO_ROOT = str(Path(__file__).parent.parent.parent.resolve())

# ---------------------------------------------------------------------------
# Quota / rate-limit error detection
# ---------------------------------------------------------------------------

# Patterns that indicate the LLM provider has no remaining quota.
# When matched, run_once will wait and retry instead of raising RuntimeError.
_QUOTA_ERROR_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"exceeded your monthly token limit",
        r"exceeded your current quota",
        r"insufficient.quota",
        r"quota.exceeded",
        r"billing hard limit",
        r"credit balance is too low",
        r"out of credits",
        r"rate.limit.exceeded",
        r"429",                     # HTTP Too Many Requests often accompanies quota errors
        r"payment required",        # HTTP 402
    ]
]

_QUOTA_RETRY_INTERVAL_SECONDS: int = int(os.getenv("LLM_QUOTA_RETRY_INTERVAL", "300"))  # 5 min default
_QUOTA_MAX_RETRIES: int = int(os.getenv("LLM_QUOTA_MAX_RETRIES", "288"))               # ~24 h at 5 min


def _is_quota_error(text: str) -> bool:
    """Return True if text contains a quota/billing exhaustion message."""
    for pattern in _QUOTA_ERROR_PATTERNS:
        if pattern.search(text):
            return True
    return False


_ALLOW_ALL_OPENCODE_PERMISSION = {
    "bash": "allow", "read": "allow", "edit": "allow", "task": "allow",
    "glob": "allow", "grep": "allow", "list": "allow",
    "external_directory": "allow", "todowrite": "allow", "todoread": "allow",
    "question": "allow", "webfetch": "allow", "websearch": "allow",
    "codesearch": "allow", "lsp": "allow", "doom_loop": "allow", "skill": "allow",
}


def _resolve_cli(command_name: str) -> str:
    """Resolve CLI binary path, preferring .cmd on Windows."""
    if os.name == "nt":
        cmd_candidate = shutil.which(f"{command_name}.cmd")
        if cmd_candidate:
            return cmd_candidate
    resolved = shutil.which(command_name)
    return resolved if resolved else command_name

def run_once(
    target: LLMTarget,
    prompt: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    timeout: float | None = 1800,
    encoding: str = "utf-8",
    quota_retry_interval: int | None = None,
    quota_max_retries: int | None = None,
) -> str:
    """
    Invoke a CLI-based LLM agent and return its stdout as a string.

    Args:
        target:   Which CLI tool to call.
        prompt:   The user prompt (appended as final argument).
        model:    Optional model override (ignored for targets that manage models internally).
        cwd:      Working directory for the subprocess.
        timeout:  Seconds before the process is killed (default 300).
        encoding: Text encoding for stdout/stderr.

    Returns:
        Raw stdout string (may contain XML-style tags for parsing).
    """
    if not prompt.strip():
        raise ValueError("prompt must not be empty.")

    _retry_interval = quota_retry_interval if quota_retry_interval is not None else _QUOTA_RETRY_INTERVAL_SECONDS
    _max_retries    = quota_max_retries    if quota_max_retries    is not None else _QUOTA_MAX_RETRIES

    work_dir = str(Path(cwd).resolve()) if cwd else None
    effective_dir = work_dir or str(Path.cwd())

    # --- .llm_io file-based I/O setup ---
    run_id = uuid.uuid4().hex[:8]
    io_dir = Path(effective_dir) / ".llm_io"
    io_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = io_dir / f"prompt_{run_id}.txt"
    output_file = io_dir / f"output_{run_id}.txt"
    prompt_file.write_text(prompt, encoding=encoding)

    stdin_input: str | None = None
    env = dict(os.environ)

    try:
        if target == LLMTarget.CLAUDE:
            command = [_resolve_cli("claude"), "--print", "--dangerously-skip-permissions"]
            if model:
                command.extend(["--model", model])
            # Pass prompt via input= (string) instead of a file handle to avoid
            # Windows file-locking (WinError 32) when the outer finally deletes the file.
            stdin_input = prompt_file.read_text(encoding=encoding)

        elif target == LLMTarget.GEMINI:
            command = [_resolve_cli("gemini"), "--approval-mode", "auto_edit",
                       "--prompt", prompt_file.read_text(encoding=encoding)]

        elif target == LLMTarget.CODEX:
            # Use repo root as cwd so AGENTS.md loads and enables tool use (same as aa.py).
            # Prompt must have no newlines — codex.cmd (Windows batch) truncates at the first
            # newline in a command-line argument.  Prompt templates are already single-line;
            # strip() + replace as a safety net.
            # Absolute paths in the prompt + AGENTS.md Output Path Rule prevent writes to
            # wrong locations when codex scans the repo.
            work_dir = _REPO_ROOT
            command = [_resolve_cli("codex"), "exec", "--dangerously-bypass-approvals-and-sandbox",
                       prompt_file.read_text(encoding=encoding).strip().replace("\n", " ")]

        elif target == LLMTarget.OPENCODE:
            env.setdefault("OPENCODE_PERMISSION", json.dumps(_ALLOW_ALL_OPENCODE_PERMISSION))
            runtime_root = Path(effective_dir).resolve() / _OPENCODE_RUNTIME_DIR
            for subdir in ("config", "data", "state"):
                (runtime_root / subdir).mkdir(parents=True, exist_ok=True)
            env.setdefault("XDG_CONFIG_HOME", str(runtime_root / "config"))
            env.setdefault("XDG_DATA_HOME",   str(runtime_root / "data"))
            env.setdefault("XDG_STATE_HOME",  str(runtime_root / "state"))
            # Pass prompt via stdin to avoid Windows console encoding issues with non-ASCII chars
            command = [_resolve_cli("opencode"), "run",
                       "--dir", effective_dir, "--format", "json", "-"]
            stdin_input = prompt_file.read_text(encoding=encoding)

        elif target == LLMTarget.COPILOT:
            command = [_resolve_cli("copilot"), "-p", prompt_file.read_text(encoding=encoding),
                       "--allow-all", "--no-ask-user", "--output-format", "text", "--silent",
                       "--add-dir", effective_dir]
            if model:
                command.extend(["--model", model])

        else:
            raise ValueError(f"Unsupported LLM target: {target}")

        logger.info(
            "run_once [%s] cwd=%s command=%s",
            target.value,
            work_dir or "(inherit)",
            " ".join(str(c) for c in command),
        )
        logger.debug("run_once [%s] prompt_file=%s\n%s", target.value, prompt_file, prompt)

        # --- Quota-aware retry loop ---
        completed = None
        for quota_attempt in range(_max_retries + 1):
            try:
                completed = subprocess.run(
                    command,
                    input=stdin_input,
                    capture_output=True,
                    text=True,
                    encoding=encoding,
                    cwd=work_dir,
                    env=env,
                    timeout=timeout,
                )
            except Exception as e:
                logging.error("execute cmd exception: %s", e)
                raise

            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                stdout = (completed.stdout or "").strip()
                parts = [s for s in [stderr, stdout] if s]
                detail = "\n".join(parts) if parts else "(no output)"

                if _is_quota_error(detail):
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    logger.warning(
                        "[QUOTA EXHAUSTED] %s | time=%s | attempt=%d/%d | "
                        "Retrying in %d s. Detail: %s",
                        target.value, ts,
                        quota_attempt + 1, _max_retries,
                        _retry_interval, detail[:300],
                    )
                    if quota_attempt < _max_retries:
                        time.sleep(_retry_interval)
                        continue
                    raise RuntimeError(
                        f"{target.value} quota exhausted — max retries ({_max_retries}) reached "
                        f"(last checked {ts}). Last error: {detail[:300]}"
                    )

                raise RuntimeError(
                    f"{target.value} CLI failed (exit {completed.returncode}): {detail[:500]}"
                )

            break  # subprocess succeeded — exit retry loop

        raw_stdout = (completed.stdout or "").strip()

        # OpenCode emits NDJSON — extract text chunks
        if target == LLMTarget.OPENCODE and raw_stdout:
            try:
                chunks = []
                for line in raw_stdout.splitlines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("type") == "error":
                        msg = (event.get("error") or {}).get("data", {}).get("message", "")
                        raise RuntimeError(str(msg))
                    message = event.get("message")
                    if isinstance(message, dict):
                        for item in (message.get("content") or []):
                            if isinstance(item, dict) and item.get("type") == "text":
                                chunks.append(str(item["text"]))
                if chunks:
                    raw_stdout = "\n".join(chunks).strip()
            except json.JSONDecodeError:
                pass

        logger.info(
            "run_once [%s] completed. stdout_len=%d stderr_len=%d",
            target.value, len(raw_stdout), len((completed.stderr or "").strip()),
        )
        logger.debug("run_once [%s] stdout:\n%s", target.value, raw_stdout[:2000])
        output_file.write_text(raw_stdout, encoding=encoding)
        return output_file.read_text(encoding=encoding)

    finally:
        prompt_file.unlink(missing_ok=True)
        output_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Codex trust helper
# ---------------------------------------------------------------------------

def _get_codex_workspace() -> str:
    """
    Return the path to the dedicated codex workspace directory.

    The workspace lives at ${VOLUME_BASE_DIR}/codex-workspace/ and contains:
      - AGENTS.md: minimal agent instructions (no project catalogue)
      - .codex/skills/windows-text-read-bat/: skill that enables agentic tool mode

    Returns the workspace path string (may not exist if VOLUME_BASE_DIR is unset).
    """
    volume_base = os.getenv("VOLUME_BASE_DIR", "./data")
    workspace = Path(volume_base) / "codex-workspace"
    _ensure_codex_trusted(str(workspace))
    return str(workspace)


def _ensure_codex_trusted(directory: str) -> None:
    """
    Ensure `directory` is listed as a trusted project in ~/.codex/config.toml.

    Codex only executes tools (file writes, shell commands) inside trusted directories.
    If the directory is not yet listed, append it.  Safe to call repeatedly.
    Never raises — failures are logged as warnings.
    """
    try:
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.exists():
            logger.debug("_ensure_codex_trusted: config not found at %s, skipping.", config_path)
            return

        content = config_path.read_text(encoding="utf-8")
        # Codex stores paths with various quoting styles; normalise to forward slashes for comparison.
        norm = str(Path(directory).resolve()).replace("\\", "/")
        if norm.lower() in content.lower() or directory.lower() in content.lower():
            logger.debug("_ensure_codex_trusted: '%s' already trusted.", directory)
            return

        # Append a new [projects.'<dir>'] section with trust_level = "trusted"
        resolved = str(Path(directory).resolve())
        entry = f"\n[projects.'{resolved}']\ntrust_level = \"trusted\"\n"
        config_path.write_text(content + entry, encoding="utf-8")
        logger.info("_ensure_codex_trusted: added '%s' to codex trusted projects.", resolved)
    except Exception as e:
        logger.warning("_ensure_codex_trusted failed for '%s': %s", directory, e)
