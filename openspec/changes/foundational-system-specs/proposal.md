## Why

The Agentic Research Workflow Engine has been built through Phases 0–2.5 without formal technical specifications. This change retroactively establishes foundational specs for the completed system, creating a single source of truth for architecture, APIs, plugin contracts, and CLI behaviour — enabling Phase 3 plugin authors and Phase 4 contributors to work from documented contracts rather than reading implementation code.

## What Changes

- Establish specification documents for every major capability already implemented
- No code changes; this is a documentation-only change that captures existing behaviour as formal specs
- Provides the baseline against which Phase 3 (Real Plugin) and Phase 4 (Optional Enhancements) changes can be written as delta specs

## Capabilities

### New Capabilities

- `workflow-graph`: LangGraph-driven research loop (plan → implement → test → analyze → revise/summarize), routers, and state definition (`ResearchState`)
- `plugin-interface`: `ResearchPlugin` ABC contract, `@register` decorator, and `resolve()` registry — the formal plugin extension point
- `hitl-interrupts`: Two HITL interrupt points (Plan Review and Loop Review), resume command shapes, and `Command(resume=…)` protocol
- `persistence-schema`: PostgreSQL schema — `projects`, `loop_metrics`, `checkpoint_decisions` tables plus LangGraph-managed checkpoint tables
- `api-server`: FastAPI endpoints (`POST /resume`, `POST /planka-webhook`) request/response contracts
- `cli-commands`: `start`, `status`, `approve`, `plugins` CLI commands with all flags and exit codes

### Modified Capabilities

<!-- No existing specs — this is the first spec creation for this project -->

## Impact

- `framework/graph.py`, `framework/plugin_interface.py`, `framework/plugin_registry.py` — captured as specs, no code changes
- `framework/api/server.py` — API contracts formalised
- `cli/main.py` — CLI contract formalised
- `db/migrations/001_business_schema.sql` — schema formalised
- Future plugin authors (`projects/*/plugin.py`) — must conform to `plugin-interface` spec
