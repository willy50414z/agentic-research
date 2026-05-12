import os

from llm_eval import parse_targets
from llm_eval.llm_target import LLMTarget

_DEFAULT_TARGET = "claude"


def get_llm_target(stage: str) -> LLMTarget:
    """Return the single LLMTarget configured for the given stage.

    Reads LLM_<STAGE> from env, falls back to LLM_DEFAULT, then 'claude'.
    Raises ValueError if the env value contains multiple targets (use get_llm_targets instead).
    """
    targets = get_llm_targets(stage)
    if len(targets) != 1:
        key = f"LLM_{stage.upper()}"
        raise ValueError(
            f"{key} must specify exactly one target for stage '{stage}', "
            f"got {[t.value for t in targets]}. Use get_llm_targets() for fallback."
        )
    return targets[0]


def get_llm_targets(stage: str) -> list[LLMTarget]:
    """Return the ordered LLMTarget list configured for the given stage.

    A comma-separated value (e.g. 'claude,gemini') enables fallback via run_with_fallback.
    Reads LLM_<STAGE> from env, falls back to LLM_DEFAULT, then 'claude'.
    """
    key = f"LLM_{stage.upper()}"
    raw = os.getenv(key) or os.getenv("LLM_DEFAULT", _DEFAULT_TARGET)
    return parse_targets(raw)


def get_all_configured_targets() -> list[LLMTarget]:
    """Return deduplicated list of every LLMTarget referenced by any LLM_* env var.

    Used by preflight to verify all configured LLMs are reachable.
    Falls back to [LLMTarget(_DEFAULT_TARGET)] when no LLM_* vars are set.
    Invalid values are silently skipped.
    """
    seen: set[LLMTarget] = set()
    result: list[LLMTarget] = []
    for key, value in os.environ.items():
        if not key.startswith("LLM_") or not value:
            continue
        try:
            for target in parse_targets(value):
                if target not in seen:
                    seen.add(target)
                    result.append(target)
        except ValueError:
            pass
    return result or parse_targets(_DEFAULT_TARGET)
