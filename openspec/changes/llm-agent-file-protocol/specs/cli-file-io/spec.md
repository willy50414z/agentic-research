## ADDED Requirements

### Requirement: run_once accepts optional cwd parameter
`run_once` SHALL accept `cwd: str | None = None`. When provided, the subprocess SHALL run
with that as its working directory. When omitted, behaviour SHALL be identical to current.

#### Scenario: Subprocess runs in supplied cwd
- **WHEN** `run_once` is called with `cwd="/some/project"`
- **THEN** the subprocess is launched with working directory `/some/project`

#### Scenario: cwd omitted — backward-compatible
- **WHEN** `run_once` is called without `cwd`
- **THEN** subprocess working directory is the process current directory (unchanged)

### Requirement: Prompt written to .llm_io file before invocation
`run_once` SHALL write the prompt to `<effective_dir>/.llm_io/prompt_<run_id>.txt` (UTF-8)
before constructing or executing any CLI command. `run_id` SHALL be `uuid.uuid4().hex[:8]`.
The `.llm_io/` directory SHALL be created automatically with `parents=True, exist_ok=True`.

#### Scenario: Prompt file created
- **WHEN** `run_once` is called with a non-empty prompt
- **THEN** `<cwd>/.llm_io/prompt_<run_id>.txt` exists containing the prompt before subprocess launch

#### Scenario: Empty prompt rejected
- **WHEN** `run_once` is called with empty or whitespace-only prompt
- **THEN** `ValueError` is raised and no file is written

### Requirement: CLAUDE reads prompt via stdin
When `target == LLMTarget.CLAUDE`, `run_once` SHALL NOT include prompt as a CLI argument.
It SHALL open the prompt file and pass it as `stdin` to the subprocess.

#### Scenario: Claude invoked without prompt arg
- **WHEN** `target` is `LLMTarget.CLAUDE`
- **THEN** the command list does NOT contain the prompt string
- **THEN** `subprocess.run` receives `stdin=<open prompt file handle>`

#### Scenario: stdin handle closed after subprocess
- **WHEN** the subprocess completes (success or failure)
- **THEN** the stdin file handle is closed

### Requirement: Non-CLAUDE CLIs read prompt file and pass as arg
For GEMINI, CODEX, OPENCODE, and COPILOT, `run_once` SHALL read the prompt file content
and pass it as the appropriate CLI argument.

#### Scenario: Prompt file content matches original prompt
- **WHEN** `run_once` is called for a non-CLAUDE target
- **THEN** the CLI arg value is identical to the original `prompt` parameter

### Requirement: Stdout mirrored to .llm_io output file
`run_once` SHALL write processed stdout to `<effective_dir>/.llm_io/output_<run_id>.txt`
after the subprocess exits successfully, and SHALL return that file's content.

#### Scenario: Output file written and returned
- **WHEN** CLI exits with returncode 0
- **THEN** `output_<run_id>.txt` is written with stdout content
- **THEN** return value of `run_once` equals content of that file

### Requirement: .llm_io files deleted after call
Both `prompt_<run_id>.txt` and `output_<run_id>.txt` SHALL be deleted in a `finally` block
regardless of success or failure.

#### Scenario: Files cleaned up on success
- **WHEN** CLI call completes successfully
- **THEN** neither file exists after `run_once` returns

#### Scenario: Files cleaned up on failure
- **WHEN** CLI call raises `RuntimeError` or `subprocess.TimeoutExpired`
- **THEN** neither file exists after the exception propagates

### Requirement: CLI provider callables forward **kwargs to run_once
All CLI provider callables (`_claude_cli`, `_gemini_cli`, `_codex_cli`, `_opencode_cli`)
SHALL accept `**kwargs` and forward them to `run_once`, enabling callers to pass `cwd`.

#### Scenario: cwd forwarded through provider callable
- **WHEN** a CLI provider callable is invoked as `llm_fn(prompt, cwd="/some/dir")`
- **THEN** `run_once` is called with `cwd="/some/dir"`

### Requirement: API provider callables tolerate cwd kwarg
API provider callables (`_claude_api`, `_gemini_api`, etc.) SHALL accept `**kwargs`
and silently ignore them.

#### Scenario: API provider ignores cwd
- **WHEN** an API provider callable is invoked with `cwd="/some/dir"`
- **THEN** the kwarg is ignored and the API call proceeds normally
