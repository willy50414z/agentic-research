"""
framework/llm_providers.py

Centralised LLM provider factory.

Supported providers:
    claude-cli    — Claude Code CLI  (claude --print)
    claude-api    — Anthropic SDK    (ANTHROPIC_API_KEY)
    opencode-cli  — OpenCode CLI     (opencode run --format json)
    opencode-api  — OpenAI-compat.   (OPENCODE_API_URL / OPENCODE_API_KEY / OPENCODE_MODEL)
    gemini-cli    — Gemini CLI       (gemini --approval-mode yolo)
    gemini-api    — Google GenAI SDK (GEMINI_API_KEY / GEMINI_MODEL)

Usage:
    from framework.llm_providers import LLMProviderFactory

    fn = LLMProviderFactory.build("claude-api")
    print(fn("Hello"))

For testing, see tests/test_llm_providers.py.
"""

from __future__ import annotations

import os
import logging
from typing import Callable

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS: list[str] = [
    "claude-cli",
    "claude-api",
    "codex-cli",
    "codex-api",
    "opencode-cli",
    "opencode-api",
    "gemini-cli",
    "gemini-api",
]


class LLMProviderFactory:
    """Build a (prompt: str) -> str callable for a named provider."""

    @staticmethod
    def build(provider: str) -> Callable[[str], str] | None:
        """
        Return a callable for *provider*, or None if it cannot be initialised
        (missing API key, CLI not on PATH, missing dependency, etc.).
        """
        try:
            builders: dict[str, Callable] = {
                "claude-cli":   LLMProviderFactory._claude_cli,
                "claude-api":   LLMProviderFactory._claude_api,
                "codex-cli":    LLMProviderFactory._codex_cli,
                "codex-api":    LLMProviderFactory._codex_api,
                "opencode-cli": LLMProviderFactory._opencode_cli,
                "opencode-api": LLMProviderFactory._opencode_api,
                "gemini-cli":   LLMProviderFactory._gemini_cli,
                "gemini-api":   LLMProviderFactory._gemini_api,
            }
            builder = builders.get(provider)
            if builder is None:
                logger.warning("Unknown provider '%s'. Supported: %s", provider, SUPPORTED_PROVIDERS)
                return None
            return builder()
        except Exception as e:
            logger.debug("Provider '%s' unavailable: %s", provider, e)
            return None

    @staticmethod
    def ping(provider: str, timeout: float = 20.0) -> bool:
        """
        Return True if *provider* is reachable and authenticated right now.

        For CLI providers a tiny prompt is sent with a short timeout to catch
        cases where the CLI is not logged in (which would otherwise block
        indefinitely waiting for browser auth).
        For API providers the check is instant (key presence only).
        """
        from framework.llm_agent.llm_target import LLMTarget

        _CLI_TARGET: dict[str, LLMTarget] = {
            "claude-cli":   LLMTarget.CLAUDE,
            "gemini-cli":   LLMTarget.GEMINI,
            "codex-cli":    LLMTarget.CODEX,
            "opencode-cli": LLMTarget.OPENCODE,
        }

        try:
            fn = LLMProviderFactory.build(provider)
            if fn is None:
                return False
            target = _CLI_TARGET.get(provider)
            if target is not None:
                from framework.llm_agent.llm_svc import run_once
                run_once(target, "ping", timeout=timeout)
            return True
        except Exception as e:
            logger.info("Provider '%s' ping failed: %s", provider, e)
            return False

    # ------------------------------------------------------------------
    # CLI providers — delegate to llm_svc.run_once for correct arg format
    # ------------------------------------------------------------------

    @staticmethod
    def _claude_cli() -> Callable[[str], str]:
        from framework.llm_agent.llm_svc import run_once
        from framework.llm_agent.llm_target import LLMTarget

        def _fn(prompt: str) -> str:
            return run_once(LLMTarget.CLAUDE, prompt)

        return _fn

    @staticmethod
    def _codex_cli() -> Callable[[str], str]:
        from framework.llm_agent.llm_svc import run_once
        from framework.llm_agent.llm_target import LLMTarget

        def _fn(prompt: str) -> str:
            return run_once(LLMTarget.CODEX, prompt)

        return _fn

    @staticmethod
    def _codex_api() -> Callable[[str], str] | None:
        """
        OpenAI Codex / OpenAI-compatible API endpoint.
        Env vars:
            CODEX_API_URL   — base URL  (default: https://api.openai.com/v1)
            CODEX_API_KEY   — API key   (falls back to OPENAI_API_KEY)
            CODEX_MODEL     — model     (default: codex-mini-latest)
        """
        import httpx

        base_url = os.getenv("CODEX_API_URL", "https://api.openai.com/v1").rstrip("/")
        api_key  = os.getenv("CODEX_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.debug("codex-api: CODEX_API_KEY / OPENAI_API_KEY not set")
            return None
        model = os.getenv("CODEX_MODEL", "codex-mini-latest")

        def _fn(prompt: str) -> str:
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                },
                timeout=120,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        return _fn

    @staticmethod
    def _opencode_cli() -> Callable[[str], str]:
        from framework.llm_agent.llm_svc import run_once
        from framework.llm_agent.llm_target import LLMTarget

        def _fn(prompt: str) -> str:
            return run_once(LLMTarget.OPENCODE, prompt)

        return _fn

    @staticmethod
    def _gemini_cli() -> Callable[[str], str]:
        from framework.llm_agent.llm_svc import run_once
        from framework.llm_agent.llm_target import LLMTarget

        def _fn(prompt: str) -> str:
            return run_once(LLMTarget.GEMINI, prompt)

        return _fn

    # ------------------------------------------------------------------
    # API providers
    # ------------------------------------------------------------------

    @staticmethod
    def _claude_api() -> Callable[[str], str] | None:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.debug("claude-api: ANTHROPIC_API_KEY not set")
            return None
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        client = anthropic.Anthropic(api_key=api_key)

        def _fn(prompt: str) -> str:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text

        return _fn

    @staticmethod
    def _opencode_api() -> Callable[[str], str]:
        """
        OpenAI-compatible endpoint.
        Env vars:
            OPENCODE_API_URL   — base URL (default: http://localhost:11434/v1)
            OPENCODE_API_KEY   — bearer token (default: opencode)
            OPENCODE_MODEL     — model name  (default: llama3.2)
        """
        import httpx

        base_url = os.getenv("OPENCODE_API_URL", "http://localhost:11434/v1").rstrip("/")
        api_key  = os.getenv("OPENCODE_API_KEY", "opencode")
        model    = os.getenv("OPENCODE_MODEL", "llama3.2")

        def _fn(prompt: str) -> str:
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                },
                timeout=120,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        return _fn

    @staticmethod
    def _gemini_api() -> Callable[[str], str] | None:
        import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.debug("gemini-api: GEMINI_API_KEY / GOOGLE_API_KEY not set")
            return None
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)

        def _fn(prompt: str) -> str:
            return model.generate_content(prompt).text

        return _fn
