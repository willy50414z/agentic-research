## ADDED Requirements

### Requirement: Spec review rules stored in .ai/rules/spec-review.md
The system SHALL maintain `.ai/rules/spec-review.md` containing all rules that govern
LLM-based spec review. The file SHALL be referenced via `@./rules/spec-review.md` in
`knowledge-base/agent_cli_file/catalogue.md` so it loads automatically through the
`CLAUDE.md` → `catalogue.md` import chain on project startup.

#### Scenario: Rules loaded on startup
- **WHEN** Claude Code starts in the project root
- **THEN** `.ai/rules/spec-review.md` content is in context via the import chain

#### Scenario: Rules cover required dimensions
- **WHEN** `.ai/rules/spec-review.md` is read
- **THEN** it contains rules for: domain identification, filling known requirements,
  applying domain defaults for optional items, threshold for raising questions (only
  non-inferable gaps), and output file conventions (`reviewed_spec.md`, `status_*.txt`)

### Requirement: Spec review prompt does not embed rules inline
`framework/prompts/spec_agent_primary.txt` SHALL reference `.ai/rules/spec-review.md`
by path and SHALL NOT embed review rules as inline text.

#### Scenario: Prompt is concise
- **WHEN** `spec_agent_primary.txt` is read
- **THEN** it is ≤ 25 lines and contains no inline rule text
- **THEN** it instructs the LLM to read `.ai/rules/spec-review.md` before reviewing
