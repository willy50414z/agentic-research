from enum import Enum


class LLMTarget(Enum):
    CLAUDE   = "claude"
    GEMINI   = "gemini"
    CODEX    = "codex"
    OPENCODE = "opencode"
    COPILOT  = "copilot"
