## Context

The Agentic Research Workflow Engine (Phases 0–2.5) was built iteratively without upfront formal specification. The system is now stable and working end-to-end: LangGraph drives a research loop, PostgreSQL persists all state, a CLI provides HITL controls, and a thin FastAPI server bridges the CLI with optional Planka integration. Phase 3 (real plugin) and Phase 4 (enhancements) are next.

This design captures the key architectural decisions already made and the trade-offs accepted, so future contributors understand the *why* behind each choice.

## Goals / Non-Goals

**Goals:**
- Document every architectural decision in the existing system with its rationale
- Identify known technical debt and its accepted risk level
- Give Phase 3 plugin authors a complete picture of the plugin contract

**Non-Goals:**
- Changing any existing code (this is documentation-only)
- Designing Phase 3 or Phase 4 features (separate changes)
- Covering deployment operations (see docker-compose.yml)

## Decisions

### D1 — LangGraph + PostgresSaver as sole persistence layer

**Decision:** Use LangGraph's `PostgresSaver` checkpointer as the primary state persistence mechanism. Remove a planned custom `loop_state` table.

**Rationale:** `PostgresSaver` provides thread-level durable state, resume-after-crash, and full history for free. Adding a custom loop table on top would duplicate data and add ~200 lines of synchronisation code.

**Alternatives considered:**
- Redis checkpointer — no durable persistence for long-running research
- SQLite — not suitable for Docker + concurrent processes
- Custom loop table — rejected; `PostgresSaver` already stores everything

**Accepted trade-off:** Single DB connection per process (module-level `psycopg.connect`). Acceptable for single-process use; Phase 4 should migrate to a connection pool if multi-process scaling is required.

---

### D2 — `interrupt()` in node body vs `interrupt_before`

**Decision:** HITL pauses are implemented by calling `langgraph.types.interrupt()` inside the node function body, not via the graph-level `interrupt_before` parameter.

**Rationale:** `interrupt_before` pauses unconditionally before a node runs. The plan-review interrupt must fire only when `needs_human_approval=True`; calling `interrupt()` conditionally inside `implement_node` achieves this without extra routing.

**Alternatives considered:**
- `interrupt_before=["implement"]` + state flag checked externally — more coupling, harder to read
- Separate "gate" node — extra graph complexity for no benefit

---

### D3 — Framework/plugin separation

**Decision:** `framework/` contains zero business logic. All domain knowledge lives in `projects/<name>/plugin.py`. The framework exposes a single `ResearchPlugin` ABC.

**Rationale:** Enables multiple concurrent research domains (quant, NLP, etc.) without framework changes. Plugins are hot-swappable; the framework is stable.

**Accepted trade-off:** Plugin authors must understand LangGraph state conventions (state dict in → state dict out), but this is documented in `plugin_interface.py` docstrings and the `plugin-interface` spec.

---

### D4 — FastAPI replaces n8n

**Decision:** A ~80-line `framework/api/server.py` handles `/resume` and `/planka-webhook`, replacing a planned n8n workflow automation layer.

**Rationale:** n8n added infrastructure complexity (another container, credential management, visual pipeline) for a job that is literally two HTTP endpoints. The FastAPI approach is stateless, testable, and co-located with the application code.

**Alternatives considered:**
- n8n — rejected after scoping; overkill for two endpoints
- Celery task queue — no async requirements in current scope

---

### D5 — stdout tag parsing (`<RESULT>`) for LLM output

**Decision:** LLM node outputs are parsed by extracting structured tags from stdout (e.g., `<RESULT>`, `<PLAN>`) via `framework/tag_parser.py`.

**Rationale:** JSON-mode LLM output is prone to hallucinated structure under longer context. Tag delimiters are explicit and forgiving — the parser can extract the first matching tag even if surrounding prose is noisy.

**Alternatives considered:**
- Pydantic `model=` structured output — model-specific, breaks portability
- JSON parsing of full output — fragile under long generations

---

### D6 — Planka is fully optional

**Decision:** `notify_planka_node` skips card creation if `PLANKA_API_URL` is not set. The system runs correctly without Planka.

**Rationale:** Planka is a convenience layer for visual review. Forcing it as a dependency would break Phase 1–2 workflows where the operator only has CLI access.

**Implementation:** Environment variable presence check at node runtime; no config validation at startup.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Single DB connection | Module-level `psycopg.connect`; safe for single-process. Phase 4: migrate to `AsyncConnectionPool`. |
| In-process graph cache | `get_or_build_graph()` cache lost on restart; graph rebuilds from checkpoint. Acceptable because `PostgresSaver` holds all durable state. |
| FAIL loops not recorded | `loop_metrics` only records PASS results (written by the framework `record_metrics` node, which only fires after `summarize`). A TERMINATE during a FAIL loop leaves no metric row. Risk: incomplete analytics. Mitigation: acceptable for Phase 3; Phase 4 should add FAIL metric recording. |
| `loop_goal` drift | `plan_node` appends replan notes to `loop_goal` on each replan. After many replans the field grows. `revise_node` was fixed not to accumulate, but `plan_node` replan path still appends. Future cleanup: cap goal length or store notes separately. |
| Plugin import coupling | `cli/main.py` imports `projects.dummy.plugin` by name. Each new plugin must be added to the import list. Phase 3: replace with auto-discovery via `importlib`. |
