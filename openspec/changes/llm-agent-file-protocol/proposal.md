## Why

The current LLM invocation pattern in this project has three compounding problems,
visible in `framework.api.server._run_spec_review_bg` as the primary example:

1. **Prompt delivery is brittle**: Prompts are passed as CLI arguments — silently truncated
   by OS arg-length limits for long inputs (e.g. full spec.md contents).
2. **Review rules are inlined**: All spec review logic lives in a large prompt string.
   Rules are duplicated, hard to update, and unavailable to other agents or tooling.
3. **Status is inferred from stdout patterns**: Whether a review passed or needs input is
   determined by parsing special character sequences from LLM output — fragile, format-
   dependent, and impossible to extend without changing parsers.

The fix is a single coherent protocol: the LLM runs as an agent in a working directory,
receives input via files, writes output files, and signals status via empty `status_*.txt`
files. Rules live in `.ai/rules/` and are loaded automatically — never embedded in prompts.
This protocol applies identically to all CLI targets, not just CLAUDE.

## What Changes

**Infrastructure layer (`llm_svc` + `llm_providers`)**
- `run_once` gains an optional `cwd` parameter; subprocess runs in that directory
- Prompt is written to `<cwd>/.llm_io/prompt_<run_id>.txt` before invocation; for CLAUDE
  piped via stdin, for all other CLIs read back and passed as arg
- Stdout written to `<cwd>/.llm_io/output_<run_id>.txt`; both files deleted in `finally`
- All CLI provider callables (`_claude_cli`, `_gemini_cli`, `_codex_cli`, `_opencode_cli`)
  accept and forward `**kwargs` (including `cwd`) to `run_once`

**Rules layer (`.ai/rules/`)**
- New file `.ai/rules/spec-review.md` — spec review rules extracted from prompt text;
  referenced via `@` import in `knowledge-base/agent_cli_file/catalogue.md` so they load
  automatically through `CLAUDE.md` on project startup

**Spec review protocol (`spec_clarifier` + `framework/prompts`)**
- `framework/prompts/spec_agent_primary.txt` rewritten as a short task-dispatch prompt:
  reads rules from `.ai/rules/spec-review.md`, reads `spec.md`, writes `reviewed_spec.md`
  and one status file — no inline rules, no structured stdout format
- `reviewed_spec.md` contains: understanding of existing spec, filled requirements,
  domain defaults for optional items, questions at the end for non-inferable gaps
- Status signalled by empty files in `cwd`:
  - `status_pass.txt` — spec complete, review passed
  - `status_need_update.txt` — questions listed inside; user must respond before proceeding
- `run_spec_agent` in `spec_clarifier.py`: passes `cwd=work_dir` to `llm_fn`; deletes
  stale status files before each run; detects outcome by file existence, not stdout parsing;
  falls back to response parsing for API providers that cannot write files

## Capabilities

### New Capabilities

- `cli-file-io`: File-based prompt/output transport for all CLI LLM invocations —
  `run_once` with `cwd`, `.llm_io/` scratch directory, providers forward `**kwargs`
- `spec-review-rules`: Spec review rules in `.ai/rules/spec-review.md`, auto-loaded
  via `CLAUDE.md` → `catalogue.md` import chain
- `spec-review-file-protocol`: Agent-mode spec review — LLM reads spec + rules, writes
  `reviewed_spec.md` and `status_pass.txt` / `status_need_update.txt`; framework detects
  outcome by file existence

### Modified Capabilities

<!-- Supersedes: file-based-cli-io, spec-review-agent-mode (both deleted) -->

## Impact

- `framework/llm_agent/llm_svc.py` — `run_once`: `cwd` param, `.llm_io/` I/O
- `framework/llm_providers.py` — CLI callables forward `**kwargs`
- `framework/spec_clarifier.py` — `run_spec_agent`: cwd injection, status file check,
  pre-run cleanup, fallback to response parsing
- `framework/prompts/spec_agent_primary.txt` — rewritten as short task-dispatch
- `.ai/rules/spec-review.md` — new rules file
- `knowledge-base/agent_cli_file/catalogue.md` — add `@./rules/spec-review.md` import
- `.gitignore` — add `.llm_io/`
- **Replaces** `openspec/changes/file-based-cli-io` and `openspec/changes/spec-review-agent-mode`
