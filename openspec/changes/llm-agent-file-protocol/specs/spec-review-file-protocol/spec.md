## ADDED Requirements

### Requirement: run_spec_agent passes cwd and cleans stale status files
`run_spec_agent` SHALL derive `work_dir = str(Path(spec_path).parent)`, delete any
existing `status_pass.txt` and `status_need_update.txt` from `work_dir`, then call
`llm_fn(prompt, cwd=work_dir)`.

#### Scenario: cwd passed to CLI provider
- **WHEN** `run_spec_agent` is called with a spec path and a CLI-based `llm_fn`
- **THEN** `llm_fn` receives `cwd` equal to the directory containing the spec file

#### Scenario: Stale status files removed before run
- **WHEN** `status_pass.txt` or `status_need_update.txt` exist in `work_dir`
- **THEN** both are deleted before `llm_fn` is called

### Requirement: LLM writes reviewed_spec.md
The prompt SHALL instruct the LLM to write `reviewed_spec.md` to `cwd` containing:
- Summary of the user's intent as understood from the spec
- All research requirements determinable from the spec, with domain defaults applied
- Reasonable assumptions for optional/inferable items, explicitly labelled
- A "待釐清問題" (Questions) section at the end listing only items that MUST be
  explicitly defined and cannot be reasonably inferred

#### Scenario: reviewed_spec.md written when spec is complete
- **WHEN** the LLM determines the spec is sufficiently complete
- **THEN** `reviewed_spec.md` exists in `work_dir` with no open questions section
  (or an empty questions section)

#### Scenario: reviewed_spec.md written when input needed
- **WHEN** the LLM determines user input is required
- **THEN** `reviewed_spec.md` exists and contains a non-empty questions section

### Requirement: LLM writes status_pass.txt when spec is complete
The prompt SHALL instruct the LLM to create an empty `status_pass.txt` in `cwd` when
the spec has no non-inferable gaps.

#### Scenario: status_pass.txt created
- **WHEN** LLM determines spec is complete
- **THEN** `status_pass.txt` exists in `work_dir`
- **THEN** `status_need_update.txt` does NOT exist

### Requirement: LLM writes status_need_update.txt with questions when input required
The prompt SHALL instruct the LLM to create `status_need_update.txt` in `cwd`, with
one question per line, when non-inferable gaps prevent execution.

#### Scenario: status_need_update.txt created
- **WHEN** LLM identifies non-inferable gaps
- **THEN** `status_need_update.txt` exists in `work_dir` with one question per line
- **THEN** `status_pass.txt` does NOT exist

### Requirement: Framework detects review outcome by file existence
After `llm_fn` returns, `run_spec_agent` SHALL determine outcome by checking `work_dir`:
- `status_pass.txt` exists → `SpecAgentResult(needs_user_input=False, ...)`
- `status_need_update.txt` exists → `SpecAgentResult(needs_user_input=True, questions=[...])`
- Neither exists → fall back to `_parse_agent_response(response, original_spec)`

`enhanced_spec_md` SHALL be `reviewed_spec.md` content if that file exists, else `original_spec`.

#### Scenario: Pass detected by file
- **WHEN** `status_pass.txt` exists after `llm_fn` returns
- **THEN** `SpecAgentResult.needs_user_input` is `False`
- **THEN** `SpecAgentResult.enhanced_spec_md` is content of `reviewed_spec.md`

#### Scenario: Needs-input detected by file
- **WHEN** `status_need_update.txt` exists after `llm_fn` returns
- **THEN** `SpecAgentResult.needs_user_input` is `True`
- **THEN** `SpecAgentResult.questions` contains non-empty lines from that file

#### Scenario: Fallback when no status file found
- **WHEN** neither status file exists (e.g. API provider)
- **THEN** `_parse_agent_response(response, original_spec)` is called and its result returned

### Requirement: Protocol is target-agnostic
The file-based review protocol (rules by reference, output via files, status via file
existence) SHALL work identically for all CLI targets.

#### Scenario: Non-CLAUDE CLI uses same protocol
- **WHEN** `llm_fn` wraps any CLI target and `cwd` is passed
- **THEN** the same file-existence status detection applies after the call
