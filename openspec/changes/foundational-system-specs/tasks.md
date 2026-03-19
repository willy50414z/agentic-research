## 1. Verify specs against implementation

- [x] 1.1 Cross-check `workflow-graph/spec.md` against `framework/graph.py`: confirm `ResearchState` keys, router names, and edge wiring match
- [x] 1.2 Cross-check `plugin-interface/spec.md` against `framework/plugin_interface.py` and `framework/plugin_registry.py`: confirm all abstract method signatures and registry behaviour
- [x] 1.3 Cross-check `hitl-interrupts/spec.md` against `framework/notify.py` and `framework/graph.py`: confirm interrupt payloads and resume action routing
- [x] 1.4 Cross-check `persistence-schema/spec.md` against `db/migrations/001_business_schema.sql` and `framework/db/queries.py`: confirm column types and constraint names
- [x] 1.5 Cross-check `api-server/spec.md` against `framework/api/server.py`: confirm endpoint paths, request/response shapes, and error codes
- [x] 1.6 Cross-check `cli-commands/spec.md` against `cli/main.py`: confirm all flag names, defaults, and output format strings

## 2. Update openspec config with project context

- [x] 2.1 Add project context block to `openspec/config.yaml` (tech stack, conventions, domain knowledge) so future change proposals are generated with accurate background

## 3. Document known technical debt in code comments

- [x] 3.1 Add `# TODO(phase4): migrate to connection pool` comment to `framework/db/connection.py` for the single-connection risk noted in design.md D1
- [x] 3.2 Add `# TODO(phase3): replace with importlib auto-discovery` comment to `cli/main.py` where plugins are imported by name
- [x] 3.3 Add `# TODO(phase4): record FAIL loop metrics` comment to `framework/graph.py` `_analyze_router` for the FAIL-not-recorded risk

## 4. Archive and sync

- [x] 4.1 Run `/opsx:verify` to confirm all spec files are consistent and complete
- [ ] 4.2 Run `/opsx:archive` to promote specs from `changes/foundational-system-specs/specs/` to `openspec/specs/`