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
import shutil
import subprocess
from pathlib import Path

from framework.llm_agent.llm_target import LLMTarget

_OPENCODE_RUNTIME_DIR = Path("data") / "tool-runtime" / "opencode"

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

def ping(target: LLMTarget):
    if LLMTarget.CLAUDE:
        completed = subprocess.run(
            "claude auth status",
            capture_output=True,
            text=True
        )
        return "\"loggedIn\": true" in completed.stdout
    else:
        logging.warn(f"{target.name} ping login has not setting")
        return False



def run_once(
    target: LLMTarget,
    prompt: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    timeout: float | None = 1800,
    encoding: str = "utf-8",
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

    work_dir = str(Path(cwd).resolve()) if cwd else None
    effective_dir = work_dir or str(Path.cwd())

    if target == LLMTarget.CLAUDE:
        command = [_resolve_cli("claude"), "--print"]
        if model:
            command.extend(["--model", model])
        command.append(prompt)

    elif target == LLMTarget.GEMINI:
        command = [_resolve_cli("gemini"), "--approval-mode", "yolo", "--sandbox", "false",
                   "--prompt", prompt]

    elif target == LLMTarget.CODEX:
        command = [_resolve_cli("codex"), "exec", "--dangerously-bypass-approvals-and-sandbox",
                   prompt]

    elif target == LLMTarget.OPENCODE:
        command = [_resolve_cli("opencode"), "run",
                   "--dir", effective_dir, "--format", "json", prompt]

    elif target == LLMTarget.COPILOT:
        command = [_resolve_cli("copilot"), "-p", prompt,
                   "--allow-all", "--no-ask-user", "--output-format", "text", "--silent",
                   "--add-dir", effective_dir]
        if model:
            command.extend(["--model", model])

    else:
        raise ValueError(f"Unsupported LLM target: {target}")

    env = dict(os.environ)
    if target == LLMTarget.OPENCODE:
        env.setdefault("OPENCODE_PERMISSION", json.dumps(_ALLOW_ALL_OPENCODE_PERMISSION))
        runtime_root = Path(effective_dir).resolve() / _OPENCODE_RUNTIME_DIR
        for subdir in ("config", "data", "state"):
            (runtime_root / subdir).mkdir(parents=True, exist_ok=True)
        env.setdefault("XDG_CONFIG_HOME", str(runtime_root / "config"))
        env.setdefault("XDG_DATA_HOME",   str(runtime_root / "data"))
        env.setdefault("XDG_STATE_HOME",  str(runtime_root / "state"))

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=encoding,
            cwd=work_dir,
            env=env,
            timeout=timeout,
        )
    except Exception as e:
        logging.error("execute cmd exception", e)

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or "(no output)"
        raise RuntimeError(
            f"{target.value} CLI failed (exit {completed.returncode}): {detail[:300]}"
        )

    stdout = (completed.stdout or "").strip()

    # OpenCode emits NDJSON — extract text chunks
    if target == LLMTarget.OPENCODE and stdout:
        try:
            chunks = []
            for line in stdout.splitlines():
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
                return "\n".join(chunks).strip()
        except json.JSONDecodeError:
            pass

    return stdout
