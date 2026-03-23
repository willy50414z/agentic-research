"""
tests/test_llm_providers.py

Manual smoke-test for all LLM providers.

Usage:
    python -m tests.test_llm_providers                     # test all
    python -m tests.test_llm_providers claude-api gemini-cli  # test subset
"""

from __future__ import annotations

import sys
from framework.llm_providers import LLMProviderFactory, SUPPORTED_PROVIDERS


class LLMProviderTester:
    """
    Test LLM providers individually or as a group.

    Example:
        tester = LLMProviderTester()
        tester.print_report()
        result = tester.test("claude-api")
        results = tester.test_chain(["claude-cli", "gemini-api"])
    """

    DEFAULT_PROMPT = "introduce yourself"

    def __init__(self, prompt: str = DEFAULT_PROMPT):
        self.prompt = prompt

    def test(self, provider: str) -> dict:
        """
        Test a single provider.

        Returns:
            {
                "provider": str,
                "ok":       bool,
                "response": str | None,
                "error":    str | None,
            }
        """
        fn = LLMProviderFactory.build(provider)
        if fn is None:
            return {
                "provider": provider,
                "ok": False,
                "response": None,
                "error": "Unavailable — check env vars or CLI installation",
            }
        try:
            response = fn(self.prompt)
            return {
                "provider": provider,
                "ok": True,
                "response": (response or "").strip()[:200],
                "error": None,
            }
        except Exception as exc:
            return {
                "provider": provider,
                "ok": False,
                "response": None,
                "error": str(exc),
            }

    def test_all(self) -> list[dict]:
        """Test every provider in SUPPORTED_PROVIDERS."""
        return [self.test(p) for p in SUPPORTED_PROVIDERS]

    def test_chain(self, providers: list[str]) -> list[dict]:
        """Test a specific ordered list of providers."""
        return [self.test(p) for p in providers]

    def print_report(self, results: list[dict] | None = None) -> None:
        """Print a human-readable test report to stdout."""
        if results is None:
            results = self.test_all()

        print(f"\n{'Provider':<20} {'Status':<8} Details")
        print("-" * 72)
        for r in results:
            status = "OK" if r["ok"] else "FAIL"
            detail = (r["response"] or "") if r["ok"] else (r["error"] or "")
            print(f"{r['provider']:<20} {status:<8} {detail}")
        ok_count = sum(1 for r in results if r["ok"])
        print(f"\n{ok_count}/{len(results)} providers OK")


if __name__ == "__main__":
    tester = LLMProviderTester()
    # targets = sys.argv[1:] or None
    targets = ["claude-cli","codex-cli","gemini-cli"]
    if targets:
        tester.print_report(tester.test_chain(targets))
    else:
        tester.print_report()
