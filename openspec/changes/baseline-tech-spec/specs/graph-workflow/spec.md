## ADDED Requirements

### Requirement: ResearchState schema
The graph uses a single `TypedDict` named `ResearchState` with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `project_id` | `str` | Unique project identifier (used as LangGraph thread_id) |
| `loop_index` | `int` | 0-based index of the current research loop |
| `loop_goal` | `str` | High-level research objective, max 500 chars (enforced by plan_node) |
| `implementation_plan` | `Optional[dict]` | Plugin-defined plan for the current loop |
| `last_result` | `str` | Routing signal: `"PASS"` \| `"FAIL"` \| `"TERMINATE"` \| `"UNKNOWN"` |
| `last_reason` | `str` | Human-readable explanation of `last_result` |
| `loop_count_since_review` | `int` | PASS loops since last Loop Review |
| `last_checkpoint_decision` | `Optional[dict]` | Last human decision (action + optional notes) |
| `needs_human_approval` | `bool` | Whether `implement_node` should interrupt for Plan Review |
| `attempt_count` | `int` | Revise attempts within current loop |
| `test_metrics` | `dict` | Latest domain metrics (plugin-defined keys, e.g. win_rate) |
| `artifacts` | `list` | Lightweight artifact references (local paths or future MinIO keys) |

The graph SHALL be compiled with a `PostgresSaver` checkpointer so that state is persisted to PostgreSQL after every node execution.

#### Scenario: State survives process restart
- **WHEN** the Python process restarts after a node completes
- **THEN** `graph.get_state(config={"configurable": {"thread_id": project_id}})` returns the last persisted state

#### Scenario: Initial state shape for start command
- **WHEN** `graph.invoke(initial_state, config)` is called with `loop_index=0` and `last_result="UNKNOWN"`
- **THEN** the graph begins execution from the START → plan edge

---

### Requirement: Node topology
The graph SHALL contain exactly the following nodes:

- `plan` — plugin's `plan_node`
- `implement` — plugin's `implement_node`
- `test` — plugin's `test_node`
- `analyze` — plugin's `analyze_node`
- `revise` — plugin's `revise_node`
- `summarize` — plugin's `summarize_node`
- `record_metrics` — framework node, writes PASS loop metrics to DB
- `record_terminate_metrics` — framework node, writes TERMINATE loop metrics to DB
- `notify_planka` — framework node, issues Loop Review interrupt

#### Scenario: Graph structure matches specification
- **WHEN** `build_graph(plugin, config)` is called
- **THEN** the compiled graph contains all nine named nodes above

---

### Requirement: Fixed edge topology
The following edges SHALL always be present regardless of plugin:

- START → `plan`
- `plan` → `implement`
- `implement` → `test`
- `test` → `analyze`
- `revise` → `implement`
- `summarize` → `record_metrics`
- `record_terminate_metrics` → END

#### Scenario: revise loops back to implement without interrupt
- **WHEN** `analyze_node` sets `last_result="FAIL"` and `revise_node` completes
- **THEN** `implement_node` is called next with `needs_human_approval=False`

---

### Requirement: Conditional routing from analyze
`analyze` SHALL route based on `last_result`:
- `"PASS"` → `summarize`
- `"FAIL"` → `revise`
- Any other value (including `"TERMINATE"`) → `record_terminate_metrics`

#### Scenario: PASS path
- **WHEN** `analyze_node` writes `last_result="PASS"`
- **THEN** `summarize` executes next, followed by `record_metrics`

#### Scenario: TERMINATE path records metrics then ends
- **WHEN** `analyze_node` writes `last_result="TERMINATE"`
- **THEN** `record_terminate_metrics` executes and the graph reaches END

---

### Requirement: Loop counter routing from record_metrics
After `record_metrics`, the graph SHALL compare `loop_count_since_review` to `review_interval`:
- If `loop_count_since_review >= review_interval` → `notify_planka` (Loop Review)
- Otherwise → `plan` (next loop)

#### Scenario: Loop Review fires at configured interval
- **WHEN** `review_interval=3` and `loop_count_since_review` reaches 3 after a PASS
- **THEN** `notify_planka` executes and the graph pauses

#### Scenario: Graph continues without Loop Review below threshold
- **WHEN** `review_interval=3` and `loop_count_since_review=1`
- **THEN** `plan` executes next (no Loop Review)

---

### Requirement: Plan Review interrupt
`implement_node` SHALL call `langgraph.types.interrupt(payload)` when `state["needs_human_approval"]` is `True`. The `payload` dict SHALL contain at minimum `{"type": "plan_review", "instruction": "..."}`.

The graph MUST be resumed with `graph.invoke(Command(resume={"action": "approve"|"reject", ...}), config)`.

After resume, `last_checkpoint_decision` SHALL be updated to the resume value and made available to the next `plan_node` call.

#### Scenario: Approve resumes to test
- **WHEN** a Plan Review interrupt is resumed with `{"action": "approve"}`
- **THEN** execution continues from `implement` → `test`

#### Scenario: Reject routes back to plan
- **WHEN** a Plan Review interrupt is resumed with `{"action": "reject", "reason": "..."}`
- **THEN** `plan_node` is called next with `last_checkpoint_decision["action"] == "reject"`

---

### Requirement: Loop Review interrupt
`notify_planka` SHALL call `langgraph.types.interrupt(payload)` unconditionally. The `payload` SHALL include `{"type": "loop_review", "loop_index": ..., "instruction": "..."}`.

The graph MUST be resumed with `graph.invoke(Command(resume={"action": "continue"|"replan"|"terminate", ...}), config)`.

After a Loop Review, `loop_count_since_review` SHALL be reset to 0 by the plugin's `plan_node` or `summarize_node`.

Routing after Loop Review:
- `"continue"` → `implement` (resumes mid-loop)
- `"replan"` → `plan` (next loop with revised goal)
- `"terminate"` → END (graph completes)

#### Scenario: Continue resumes execution
- **WHEN** Loop Review is resumed with `{"action": "continue"}`
- **THEN** `implement_node` executes next

#### Scenario: Terminate ends the graph
- **WHEN** Loop Review is resumed with `{"action": "terminate"}`
- **THEN** the graph reaches END without running any more plugin nodes

---

### Requirement: Graph cache
`get_or_build_graph(plugin, config)` SHALL cache compiled graphs in memory keyed by `plugin.name`. Subsequent calls with the same plugin name SHALL return the cached graph instance.

The cache is process-scoped and is rebuilt when the process restarts. Graph state is preserved in PostgreSQL.

#### Scenario: Second call returns same instance
- **WHEN** `get_or_build_graph` is called twice with the same plugin name
- **THEN** both calls return the same object (identity check passes)

#### Scenario: Different plugins get separate graph instances
- **WHEN** `get_or_build_graph` is called with `plugin_a` then `plugin_b`
- **THEN** each plugin name has its own entry in the cache

---

### Requirement: record_metrics node (framework-owned)
`record_metrics` SHALL write one row to `loop_metrics` after every PASS loop with:
- `loop_index = state["loop_index"] - 1` (summarize already incremented it)
- `result = state["last_result"]`
- `reason = state["last_reason"]`
- `metrics = state["test_metrics"]`
- `report_path` = path of the most recent `type: "summary"` artifact, if any

A DB write failure SHALL be logged as a warning and SHALL NOT abort the graph execution.

#### Scenario: Metrics written after PASS
- **WHEN** a loop completes with `last_result="PASS"` and `loop_index` was 0 before summarize
- **THEN** `loop_metrics` contains a row with `loop_index=0, result="PASS"`

#### Scenario: DB error is non-blocking
- **WHEN** the DB is unreachable during `record_metrics`
- **THEN** a WARNING is logged and the graph continues to the next node

---

### Requirement: record_terminate_metrics node (framework-owned)
`record_terminate_metrics` SHALL write one row to `loop_metrics` when `last_result="TERMINATE"` with:
- `loop_index = state["loop_index"]` (summarize did NOT run, so index is not incremented)
- `result = state["last_result"]` (typically `"TERMINATE"`)
- `report_path = None`

A DB write failure SHALL be logged as a warning and SHALL NOT abort graph termination.

#### Scenario: Terminate metrics recorded at correct index
- **WHEN** a TERMINATE occurs at `loop_index=1` (no summarize ran for this loop)
- **THEN** `loop_metrics` contains a row with `loop_index=1, result="TERMINATE"`
