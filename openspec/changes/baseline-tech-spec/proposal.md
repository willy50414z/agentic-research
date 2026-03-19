## Why

The Agentic Research Workflow Engine (Phases 0–4) is fully implemented but lacks formal technical specifications. Without specs, contributors cannot understand the Plugin contract, API guarantees, or schema constraints, making it difficult to extend the system or build new plugins safely.

## What Changes

- Introduce a formal **Plugin Interface spec** documenting the `ResearchPlugin` ABC contract, state read/write rules per node, and registration protocol
- Introduce a **Graph Workflow spec** documenting the node topology, routing logic, interrupt points, and state machine transitions
- Introduce a **Database Schema spec** documenting all tables (LangGraph-managed and business), column types, and write semantics
- Introduce a **REST API spec** documenting `/resume` and `/planka-webhook` endpoints, request/response shapes, and error handling
- Introduce a **CLI spec** documenting all commands, options, and expected outputs
- No breaking changes — this is documentation of existing behaviour, not new behaviour

## Capabilities

### New Capabilities

- `plugin-interface`: The `ResearchPlugin` ABC contract — six required node methods, state read/write rules per node, `@register` decorator, and plugin discovery protocol
- `graph-workflow`: The LangGraph StateGraph topology — nodes, edges, conditional routing, interrupt points, and `ResearchState` schema
- `database-schema`: All PostgreSQL tables, columns, types, write ownership, and migration protocol
- `rest-api`: FastAPI server endpoints (`/resume`, `/planka-webhook`) — HTTP methods, request bodies, response shapes, and error codes
- `cli-commands`: All Typer CLI commands (`start`, `status`, `approve`, `plugins`) — options, arguments, and example outputs

### Modified Capabilities

<!-- No existing specs to modify — this is the first set of specs for this project -->

## Impact

- No production code changes required
- `openspec/specs/` will be populated for the first time, enabling future changes to reference existing capability specs
- Enables `/opsx:verify` checks on future changes that touch these capabilities
