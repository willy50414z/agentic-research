"""
tests/test_spec_review_replay.py

手動重現 _run_spec_review_bg 中 run_spec_agent 的呼叫，方便從 log 貼入參數重跑。

用法：
    # 直接執行（不需 pytest）
    python tests/test_spec_review_replay.py \
        --spec E:/docker_data/agentic-research/1774520032/spec.md \
        --primary claude-cli \
        --secondary codex-cli

    # 或用 pytest（需要 --spec 才有意義，直接執行比較方便）
    pytest tests/test_spec_review_replay.py -s -v \
        --spec E:/docker_data/agentic-research/1774520032/spec.md
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SpecReviewReplayer:
    """
    重現 _run_spec_review_bg 中對 run_spec_agent 的呼叫。

    從 log 取得 spec_path、primary/secondary provider 名稱後，
    建立此類別並呼叫 run() 即可重現相同流程。
    """

    def __init__(
        self,
        spec_path: str,
        primary_provider: str = "claude-cli",
        secondary_provider: str | None = None,
    ):
        self.spec_path = spec_path
        self.primary_provider = primary_provider
        self.secondary_provider = secondary_provider or primary_provider

    # ------------------------------------------------------------------

    def run(self) -> dict:
        """執行 primary + secondary review，回傳結果摘要。"""
        from framework.llm_providers import LLMProviderFactory
        from framework.spec_clarifier import run_spec_agent

        logger.info("=== SpecReviewReplayer START ===")
        logger.info("spec_path      : %s", self.spec_path)
        logger.info("primary        : %s", self.primary_provider)
        logger.info("secondary      : %s", self.secondary_provider)

        spec_path = self.spec_path
        work_dir = str(Path(spec_path).parent)

        # --- Primary ---
        logger.info("--- building primary LLM fn (%s) ---", self.primary_provider)
        primary_fn = LLMProviderFactory.build(self.primary_provider)
        if primary_fn is None:
            raise RuntimeError(f"Provider '{self.primary_provider}' could not be initialised.")

        logger.info("--- calling run_spec_agent (primary) ---")
        result1 = run_spec_agent(spec_path, llm_fn=primary_fn, role="primary")
        logger.info(
            "primary result: needs_user_input=%s  questions=%s",
            result1.needs_user_input,
            result1.questions,
        )

        if result1.needs_user_input:
            logger.warning("Primary agent requested clarification — stopping after primary.")
            return {
                "stage": "primary",
                "needs_user_input": True,
                "questions": result1.questions,
                "domain": result1.domain,
                "agent_notes": result1.agent_notes,
            }

        # --- Secondary (uses reviewed_spec.md written by primary) ---
        reviewed_spec_path = str(Path(work_dir) / "reviewed_spec.md")
        if not Path(reviewed_spec_path).exists():
            logger.warning("reviewed_spec.md not found at %s — using original spec for secondary.", reviewed_spec_path)
            reviewed_spec_path = spec_path

        logger.info("--- building secondary LLM fn (%s) ---", self.secondary_provider)
        secondary_fn = LLMProviderFactory.build(self.secondary_provider)
        if secondary_fn is None:
            raise RuntimeError(f"Provider '{self.secondary_provider}' could not be initialised.")

        logger.info("--- calling run_spec_agent (secondary) ---")
        result2 = run_spec_agent(reviewed_spec_path, llm_fn=secondary_fn, role="secondary")
        logger.info(
            "secondary result: needs_user_input=%s  questions=%s",
            result2.needs_user_input,
            result2.questions,
        )

        logger.info("=== SpecReviewReplayer DONE ===")
        return {
            "stage": "secondary",
            "needs_user_input": result2.needs_user_input,
            "questions": result2.questions,
            "domain": result2.domain,
            "agent_notes": result2.agent_notes,
        }

    # ------------------------------------------------------------------

    def dry_run(self) -> None:
        """
        只印出 prompt（不呼叫 LLM），方便確認 placeholder 替換是否正確。
        """
        from framework.spec_clarifier import _load_prompt

        spec_path = self.spec_path
        work_dir = str(Path(spec_path).parent)

        for role in ("primary", "secondary"):
            template = _load_prompt(role)
            prompt = template.replace("{SPEC_PATH}", spec_path).replace("{OUTPUT_DIR}", work_dir)
            print(f"\n{'='*60}")
            print(f"ROLE: {role}  |  cwd={work_dir}")
            print(f"{'='*60}")
            print(prompt)


# ---------------------------------------------------------------------------
# pytest entry-point (skipped without --spec)
# ---------------------------------------------------------------------------

import pytest


def pytest_addoption(parser):
    parser.addoption("--spec", action="store", default=None, help="Path to spec.md to replay")


@pytest.fixture
def spec_path(request):
    return request.config.getoption("--spec")


@pytest.mark.skipif(
    not any("--spec" in a for a in sys.argv),
    reason="Pass --spec <path> to run this test",
)
class TestSpecReviewReplay:
    """pytest wrapper — run with:  pytest tests/test_spec_review_replay.py -s --spec <path>"""

    def test_dry_run(self, spec_path):
        """Verify prompt placeholder substitution without calling the LLM."""
        assert spec_path, "Provide --spec <path_to_spec.md>"
        replayer = SpecReviewReplayer(spec_path)
        replayer.dry_run()  # just prints — no assertion needed

    def test_primary_only(self, spec_path):
        """Run only the primary agent and report result."""
        assert spec_path, "Provide --spec <path_to_spec.md>"
        from framework.llm_providers import LLMProviderFactory
        from framework.spec_clarifier import run_spec_agent

        fn = LLMProviderFactory.build("claude-cli")
        assert fn is not None, "claude-cli provider not available"
        result = run_spec_agent(spec_path, llm_fn=fn, role="primary")
        logger.info("needs_user_input=%s", result.needs_user_input)
        logger.info("questions=%s", result.questions)
        logger.info("domain=%s", result.domain)

    def test_full_review(self, spec_path):
        """Run the full primary + secondary flow."""
        assert spec_path, "Provide --spec <path_to_spec.md>"
        replayer = SpecReviewReplayer(spec_path)
        result = replayer.run()
        logger.info("final result: %s", result)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay a spec review run from log parameters.")
    parser.add_argument("--spec", required=True, help="Path to spec.md (from log)")
    parser.add_argument("--primary", default="claude-cli", help="Primary LLM provider (default: claude-cli)")
    parser.add_argument("--secondary", default=None, help="Secondary LLM provider (default: same as primary)")
    parser.add_argument("--dry-run", action="store_true", help="Only print prompts, do not call LLM")
    args = parser.parse_args()

    replayer = SpecReviewReplayer(
        spec_path=args.spec,
        primary_provider=args.primary,
        secondary_provider=args.secondary,
    )

    if args.dry_run:
        replayer.dry_run()
    else:
        result = replayer.run()
        print("\n=== Final Result ===")
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
