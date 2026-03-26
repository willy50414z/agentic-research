## 1. Preparation

- [x] 1.1 Add `import uuid` to `framework/llm_agent/llm_svc.py`
- [x] 1.2 Add `.llm_io/` to `.gitignore`

## 2. run_once — cwd and .llm_io transport

- [x] 2.1 Add `cwd: str | None = None` to `run_once` signature; update `work_dir` / `effective_dir` derivation
- [x] 2.2 Compute `run_id = uuid.uuid4().hex[:8]`; create `io_dir = Path(effective_dir) / ".llm_io"` with `mkdir(parents=True, exist_ok=True)`
- [x] 2.3 Write prompt to `io_dir / f"prompt_{run_id}.txt"` (raise `ValueError` for empty prompt before writing)
- [x] 2.4 Wrap body in `try/finally`; delete both `prompt_<run_id>.txt` and `output_<run_id>.txt` in `finally`

## 3. run_once — Per-Target Prompt Delivery

- [x] 3.1 **CLAUDE**: remove positional prompt arg; open prompt file as `stdin_handle`; pass `stdin=stdin_handle`; close in `finally`
- [x] 3.2 **GEMINI**: replace `prompt` arg with `prompt_file.read_text(encoding=encoding)`
- [x] 3.3 **CODEX**: replace `prompt` arg with `prompt_file.read_text(encoding=encoding)`
- [x] 3.4 **OPENCODE**: replace `prompt` arg with `prompt_file.read_text(encoding=encoding)`; move OPENCODE env setup inside this branch
- [x] 3.5 **COPILOT**: replace `prompt` arg with `prompt_file.read_text(encoding=encoding)`

## 4. run_once — Output File

- [x] 4.1 After `raw_stdout` is finalised (including NDJSON extraction for OPENCODE), write to `io_dir / f"output_{run_id}.txt"`
- [x] 4.2 Return `output_file.read_text(encoding=encoding)`

## 5. llm_providers — kwargs forwarding

- [x] 5.1 `_claude_cli`: `def _fn(prompt, **kwargs): return run_once(LLMTarget.CLAUDE, prompt, **kwargs)`
- [x] 5.2 `_gemini_cli`: same pattern
- [x] 5.3 `_codex_cli`: same pattern
- [x] 5.4 `_opencode_cli`: same pattern
- [x] 5.5 All API providers (`_claude_api`, `_gemini_api`, `_codex_api`, `_opencode_api`): `def _fn(prompt, **kwargs)` — ignore kwargs

## 6. Rules File

- [x] 6.1 Create `.ai/rules/spec-review.md`: domain identification, filling knowns, domain defaults, question threshold, output file conventions (`reviewed_spec.md` structure, `status_pass.txt` / `status_need_update.txt`)
- [x] 6.2 Add `@.ai/rules/spec-review.md` to `CLAUDE.md` (project-level rule, cleaner than relative path through submodule)

## 7. Prompt Rewrite

- [x] 7.1 Rewrite `framework/prompts/spec_agent_primary.txt` as ≤ 25-line task-dispatch: instruct LLM to read `.ai/rules/spec-review.md`, read `spec.md`, write `reviewed_spec.md` and one status file to the working directory
- [x] 7.2 Verify no inline review rules remain in the prompt

## 8. spec_clarifier.py — Agent Protocol

- [x] 8.1 In `run_spec_agent`, derive `work_dir = str(Path(spec_path).parent)`
- [x] 8.2 Delete `status_pass.txt` and `status_need_update.txt` from `work_dir` before calling `llm_fn` (`unlink(missing_ok=True)`)
- [x] 8.3 Inject spec path and output dir into prompt (replace `{SPEC_PATH}` and `{OUTPUT_DIR}` placeholders)
- [x] 8.4 Call `llm_fn(prompt, cwd=work_dir)`
- [x] 8.5 After call: if `status_pass.txt` exists → return `SpecAgentResult(needs_user_input=False, enhanced_spec_md=reviewed_spec or original, ...)`
- [x] 8.6 If `status_need_update.txt` exists → read questions line-by-line, return `SpecAgentResult(needs_user_input=True, questions=[...], ...)`
- [x] 8.7 If neither exists → fall back to `_parse_agent_response(response, original_spec)`
- [x] 8.8 In all cases, read `reviewed_spec.md` from `work_dir` as `enhanced_spec_md` if it exists

## 9. Cleanup — Remove Superseded Changes

- [x] 9.1 Delete `openspec/changes/file-based-cli-io/` directory
- [x] 9.2 Delete `openspec/changes/spec-review-agent-mode/` directory

## 10. Verification

- [ ] 10.1 Invoke `run_once(CLAUDE, long_prompt, cwd="/tmp/test")` — confirm subprocess runs in `/tmp/test`, prompt > 8 000 chars succeeds, `.llm_io/` is empty after call
- [ ] 10.2 Invoke `llm_fn(prompt, cwd="/tmp/test")` for each CLI provider — confirm `cwd` is forwarded
- [ ] 10.3 Invoke an API provider callable with `cwd=` — confirm it is silently ignored
- [ ] 10.4 Run a full spec review via `_run_spec_review_bg` — confirm `reviewed_spec.md` and one of the two status files appear in the spec directory
- [ ] 10.5 Confirm stale status files are removed on retry
- [ ] 10.6 Confirm `spec_agent_primary.txt` has no inline rules
