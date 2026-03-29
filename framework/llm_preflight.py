"""
framework/llm_preflight.py

Pre-flight connectivity checker for all system dependencies.

Called once during FastAPI server lifespan startup. Results are cached in
{VOLUME_BASE_DIR}/preflight_cache.json keyed by SHA-256 of the LLM_CHAIN
value. Cache is reused within a 1-hour TTL; any change to LLM_CHAIN or
expiry triggers a full re-check.

Raises RuntimeError if any required service fails validation.

Usage:
    from framework.llm_preflight import preflight_check, get_preflight_results
    preflight_check(db_url, planka_url, planka_token, llm_chain_str)
    results = get_preflight_results()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 3600  # 1 hour
_last_results: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Per-service checks
# ---------------------------------------------------------------------------

def _check_claude_cli() -> dict:
    binary = _resolve_cli("claude")
    try:
        result = subprocess.run(
            [binary, "auth", "status", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and '"loggedIn": true' in result.stdout:
            return {"ok": True}
        reason = result.stderr.strip() or result.stdout.strip() or "loggedIn not true"
        return {"ok": False, "reason": reason[:200]}
    except FileNotFoundError:
        return {"ok": False, "reason": "claude CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "claude auth status timed out"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


def _check_cli_version(tool: str) -> dict:
    """Generic check: run `{tool} --version`, expect returncode 0."""
    binary = _resolve_cli(tool)
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "reason": (result.stderr or result.stdout or "non-zero exit").strip()[:200]}
    except FileNotFoundError:
        return {"ok": False, "reason": f"{tool} CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"{tool} --version timed out"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


def _check_gemini_cli() -> dict:
    return _check_cli_version("gemini")


def _check_codex_cli() -> dict:
    return _check_cli_version("codex")


def _check_opencode_cli() -> dict:
    return _check_cli_version("opencode")


def _check_copilot_cli() -> dict:
    return _check_cli_version("copilot")


def _check_api_provider(provider: str) -> dict:
    """Check that the required API key env var exists and is non-empty."""
    key_map = {
        "claude-api":    ["ANTHROPIC_API_KEY"],
        "gemini-api":    ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "codex-api":     ["CODEX_API_KEY", "OPENAI_API_KEY"],
        "opencode-api":  ["OPENCODE_API_KEY"],
    }
    keys = key_map.get(provider, [])
    for key in keys:
        if os.getenv(key):
            return {"ok": True}
    if keys:
        return {"ok": False, "reason": f"None of {keys} are set"}
    return {"ok": False, "reason": f"Unknown API provider '{provider}'"}


def _check_planka(planka_url: str, planka_token: str) -> dict:
    if not planka_url or not planka_token:
        return {"ok": False, "reason": "PLANKA_API_URL or PLANKA_TOKEN not set"}
    try:
        resp = httpx.get(
            f"{planka_url.rstrip('/')}/api/v1/users/me",
            headers={"Authorization": f"Bearer {planka_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True}
        return {"ok": False, "reason": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


def _check_database(db_url: str) -> dict:
    if not db_url:
        return {"ok": False, "reason": "DATABASE_URL not set"}
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

_CLI_CHECKERS = {
    "claude-cli":   _check_claude_cli,
    "gemini-cli":   _check_gemini_cli,
    "codex-cli":    _check_codex_cli,
    "opencode-cli": _check_opencode_cli,
    "copilot-cli":  _check_copilot_cli,
}

_API_PROVIDERS = {"claude-api", "gemini-api", "codex-api", "opencode-api"}


def _check_provider(provider: str) -> dict:
    if provider in _CLI_CHECKERS:
        return _CLI_CHECKERS[provider]()
    if provider in _API_PROVIDERS:
        return _check_api_provider(provider)
    return {"ok": False, "reason": f"Unknown provider '{provider}'"}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    volume_base = os.getenv("VOLUME_BASE_DIR", "./data")
    return Path(volume_base) / "preflight_cache.json"


def _chain_hash(llm_chain_str: str) -> str:
    return hashlib.sha256(llm_chain_str.encode()).hexdigest()[:16]


def _load_cache(chain_hash: str) -> dict | None:
    path = _cache_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("chain_hash") != chain_hash:
            return None
        validated_at = data.get("validated_at", 0)
        age = time.time() - validated_at
        if age > _CACHE_TTL_SECONDS:
            return None
        return data
    except Exception as e:
        logger.debug("preflight cache read failed: %s", e)
        return None


def _save_cache(chain_hash: str, results: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chain_hash": chain_hash,
            "validated_at": time.time(),
            "validated_at_iso": datetime.now(timezone.utc).isoformat(),
            "results": results,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("preflight cache write failed: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preflight_check(
    db_url: str,
    planka_url: str,
    planka_token: str,
    llm_chain_str: str,
) -> dict:
    """
    Run connectivity checks for all required services.

    Checks:
      - Each provider in llm_chain_str
      - Planka JWT (if planka_url and planka_token are set)
      - Database (SELECT 1)

    Uses cache if chain_hash matches and TTL has not expired.

    Raises:
        RuntimeError: if any service fails validation.

    Returns:
        dict of {service_name: {"ok": bool, "reason"?: str}}
    """
    global _last_results

    chain_hash = _chain_hash(llm_chain_str)
    cached = _load_cache(chain_hash)
    if cached:
        logger.info("preflight: using cache (validated_at=%s)", cached.get("validated_at_iso"))
        _last_results = cached["results"]
        _enforce_results(_last_results)
        return _last_results

    logger.info("preflight: running connectivity checks ...")
    results: dict[str, dict] = {}

    # LLM providers
    providers = [p.strip() for p in (llm_chain_str or "").split(",") if p.strip()]
    for provider in providers:
        result = _check_provider(provider)
        results[provider] = result
        status = "OK" if result["ok"] else f"FAIL ({result.get('reason', '')})"
        logger.info("preflight: %-20s %s", provider, status)

    # Planka
    if planka_url:
        results["planka"] = _check_planka(planka_url, planka_token)
        status = "OK" if results["planka"]["ok"] else f"FAIL ({results['planka'].get('reason', '')})"
        logger.info("preflight: %-20s %s", "planka", status)

    # Database
    results["database"] = _check_database(db_url)
    status = "OK" if results["database"]["ok"] else f"FAIL ({results['database'].get('reason', '')})"
    logger.info("preflight: %-20s %s", "database", status)

    _last_results = results
    _save_cache(chain_hash, results)
    _enforce_results(results)
    return results


def _enforce_results(results: dict) -> None:
    failed = [name for name, r in results.items() if not r.get("ok")]
    if failed:
        details = "; ".join(
            f"{name}: {results[name].get('reason', 'failed')}" for name in failed
        )
        raise RuntimeError(f"Preflight failed — {details}")


def get_preflight_results() -> dict:
    """Return the most recent preflight results (set during startup)."""
    return _last_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_cli(command_name: str) -> str:
    if os.name == "nt":
        cmd_candidate = shutil.which(f"{command_name}.cmd")
        if cmd_candidate:
            return cmd_candidate
    resolved = shutil.which(command_name)
    return resolved if resolved else command_name
