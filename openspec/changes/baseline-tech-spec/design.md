## Context

The Agentic Research Workflow Engine has been built through Phases 0–4. The codebase has stable, tested behaviour but no formal specifications. This design covers how the baseline specs are structured and what principles govern them.

The system centres on three boundaries:
1. **Framework boundary** — `framework/` owns graph topology, persistence, interrupts, and metrics. Zero business logic lives here.
2. **Plugin boundary** — `projects/<name>/plugin.py` owns all domain logic. The plugin implements exactly six node methods against the `ResearchPlugin` ABC.
3. **Infrastructure boundary** — PostgreSQL (via PostgresSaver + business schema), Docker Compose, FastAPI, and Typer CLI are the external surfaces.

## Goals / Non-Goals

**Goals:**
- Produce machine-readable specs that future changes can reference via delta specs
- Capture the *normative* behaviour of each system surface (what it SHALL do), not implementation details
- Enable `/opsx:verify` to check future changes for spec compliance
- Cover all five surfaces: Plugin Interface, Graph Workflow, Database Schema, REST API, CLI

**Non-Goals:**
- Documenting internal implementation choices (those belong in code comments or design docs for individual changes)
- Covering `projects/quant_alpha/` — that is a plugin implementation, not a framework surface
- Specifying future phases (Planka card creation, MinIO artifact storage, Langfuse tracing)

## Decisions

### Decision 1: One spec file per surface, not per file

Each spec covers a user-facing *capability* (e.g., "plugin-interface") rather than a code file (e.g., "plugin_interface.py"). This keeps specs stable as internal files move or split.

**Alternative considered**: One spec per Python module.
**Why rejected**: Too granular; modules are refactored more often than their external contracts change.

### Decision 2: Document actual behaviour, mark known gaps

The `loop_metrics` schema comment says `PASS | FAIL` but the framework now also writes `TERMINATE`. The spec documents the *actual* written values (PASS, FAIL, TERMINATE) as normative, and flags the DDL comment as stale — no code change required.

### Decision 3: State schema as part of graph-workflow spec, not plugin-interface spec

`ResearchState` is owned by the framework (`graph.py`). Plugins read and write specific keys but do not define the schema. Documenting state in `graph-workflow` prevents duplication and makes ownership clear.

### Decision 4: Planka webhook is documented as optional

`/planka-webhook` requires `PLANKA_TOKEN` and `PLANKA_API_URL` env vars. The spec captures this conditionality with SHALL/MAY language so implementors know when to skip Planka integration.

## Risks / Trade-offs

- **Specs lag reality**: Because this is a retrofit (specs written after code), there is a risk of subtle inaccuracies. Mitigation: specs were written by reading the actual source, not from memory.
- **State key ownership ambiguity**: Both framework and plugins write to `ResearchState`. The spec assigns ownership per node to reduce confusion, but this must be kept in sync as nodes evolve.
- **No automated spec-to-code verification**: Until a test harness is added, specs can drift silently. Mitigation: use `/opsx:verify` on every future change that touches a specified surface.

## Migration Plan

No production code changes. Steps:
1. Write `design.md` (this file)
2. Write five spec files under `openspec/changes/baseline-tech-spec/specs/`
3. Write `tasks.md` (verification checklist)
4. Archive — specs are promoted to `openspec/specs/`

## Open Questions

- Should `loop_metrics` schema be extended with generic JSONB `extra_metrics` to avoid future DDL migrations for new plugin metric types? (Deferred to a future change.)
