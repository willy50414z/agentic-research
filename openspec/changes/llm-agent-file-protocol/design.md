## Context

The project's LLM call path is: `llm_providers.py` → `llm_svc.run_once` → CLI subprocess.
`spec_clarifier.py` sits above this, building prompts and parsing responses for spec review.

Current state:
- `run_once` passes the prompt as a positional CLI arg (no `cwd`)
- `spec_clarifier` embeds review rules inline in a >50-line prompt
- Review outcome (`needs_user_input`) is extracted by regex from an `AGENT_META` HTML
  comment or `=== FILE: ... ===` delimiter blocks in LLM stdout
- Only the prompt-delivery side was considered for file I/O; output still uses stdout

This design documents the decisions for the unified file-based agent protocol that fixes
all three layers in one coherent change.

## Goals / Non-Goals

**Goals:**
- `run_once` accepts `cwd`; subprocess runs there; prompt in `.llm_io/`, stdout mirrored
  to `.llm_io/`; both files deleted in `finally`
- All CLI providers forward `**kwargs` → `run_once` so callers inject `cwd` transparently
- Spec review rules extracted to `.ai/rules/spec-review.md`; loaded via CLAUDE.md chain
- `spec_agent_primary.txt` becomes a short task-dispatch (≤ 20 lines)
- `run_spec_agent` passes `cwd`, cleans stale status files, detects outcome by file existence
- Protocol is identical for all CLI targets (CLAUDE, GEMINI, CODEX, OPENCODE, COPILOT)

**Non-Goals:**
- Changing API providers (no subprocess, no `cwd` concept; they fall back gracefully)
- Persistent logging of prompts/outputs (Phase 4: Langfuse/MLflow)
- Streaming or incremental output reads
- Changing the Planka attachment upload flow in `server.py`

## Decisions

### D1 — `cwd` on `run_once`, forwarded via `**kwargs` on providers

**Decision**: Add `cwd: str | None = None` to `run_once`. CLI providers return
`def _fn(prompt, **kwargs): return run_once(target, prompt, **kwargs)`.

**Rationale**: `cwd=None` is backward-compatible — existing callers unchanged. `**kwargs`
avoids a signature mismatch between CLI and API providers; API providers simply ignore it.

### D2 — `.llm_io/` scratch dir; files deleted in `finally`

**Decision**: Create `<effective_dir>/.llm_io/prompt_<run_id>.txt` and `output_<run_id>.txt`.
Delete both in `finally`. Directory itself persists (avoids TOCTOU under concurrency).

**Rationale**: Eliminates arg-length limits; provides an inspectable artefact during a run.
Ephemeral by default so no cleanup burden. `run_id = uuid.uuid4().hex[:8]` prevents
collisions under concurrent calls.

### D3 — CLAUDE reads prompt via stdin; all other CLIs read file and pass as arg

**Decision**: For CLAUDE, omit positional arg and set `stdin=open(prompt_file)`. For
GEMINI, CODEX, OPENCODE, COPILOT: `prompt_file.read_text()` passed as the existing arg.

**Rationale**: Claude CLI's `--print` mode reads stdin when no positional arg is given.
Other CLIs' stdin behaviour is undocumented; reading the file is safe and portable.

### D4 — Rules in `.ai/rules/spec-review.md`, loaded via `CLAUDE.md` chain

**Decision**: Create `.ai/rules/spec-review.md`. Add `@./rules/spec-review.md` to
`knowledge-base/agent_cli_file/catalogue.md`. Existing `CLAUDE.md` → catalogue import
chain already handles loading.

**Rationale**: Rules become independently versioned and reusable by any agent. The loading
chain is already established — no new plumbing required.

### D5 — `status_pass.txt` / `status_need_update.txt` for review outcome

**Decision**: Prompt instructs LLM to create one of two files in `cwd`:
- `status_pass.txt` (empty) — spec complete, no questions
- `status_need_update.txt` — one question per line

`run_spec_agent` checks existence: `status_pass.txt` → `needs_user_input=False`;
`status_need_update.txt` → `needs_user_input=True`, questions from file lines.

**Rationale**: File existence is binary and unambiguous. Naming convention
`status_<state>.txt` is extensible (future states: `status_blocked.txt`, etc.).

**Alternative**: Use a single `status.txt` with a keyword inside — rejected; requires
content parsing, reintroduces the fragility we're eliminating.

### D6 — Pre-run cleanup of stale status files

**Decision**: Before calling `llm_fn`, delete `status_pass.txt` and `status_need_update.txt`
from `work_dir` if they exist.

**Rationale**: A crashed run leaves orphaned status files. Pre-run cleanup is cheap and
prevents false positives on retry.

### D7 — API providers fall back to response parsing

**Decision**: If neither status file is present after `llm_fn` returns (API provider
cannot write files), fall back to `_parse_agent_response(response, original_spec)`.

**Rationale**: Preserves backward compatibility. API providers are not the primary target
for agent-mode workflows but should not hard-fail.

## Risks / Trade-offs

- **Disk I/O on every call** → negligible vs LLM round-trip (seconds).
- **`.llm_io/` orphaned on process kill** → add to `.gitignore`; `rm -rf .llm_io/` clears.
- **LLM fails to write status file** → fallback to response parsing; worst case: treated
  as `needs_user_input=True`, user is asked to move card back — recoverable.
- **reviewed_spec.md not written** → `original_spec` used as fallback.
