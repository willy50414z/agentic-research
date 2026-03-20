## ADDED Requirements

### Requirement: Graph node sequence
The system SHALL execute research loops in the following node order:
`START → plan → implement → test → analyze → [revise → implement]* → summarize → record_metrics → [notify_planka]? → plan | END`

The graph SHALL be compiled via `build_graph(plugin, config)` using a `StateGraph(ResearchState)` with a `PostgresSaver` checkpointer attached at compile time.

#### Scenario: Normal PASS loop
- **WHEN** `analyze_node` sets `last_result = "PASS"`
- **THEN** the graph routes to `summarize`, then `record_metrics`, then checks the loop counter router

#### Scenario: FAIL triggers revise
- **WHEN** `analyze_node` sets `last_result = "FAIL"`
- **THEN** the graph routes to `revise`, then back to `implement` (bypassing the plan-review interrupt)

#### Scenario: TERMINATE exits immediately
- **WHEN** `analyze_node` sets `last_result = "TERMINATE"`
- **THEN** the graph routes directly to `END` without executing `summarize` or `record_metrics`

---

### Requirement: ResearchState schema
The system SHALL use a `TypedDict` named `ResearchState` as the single shared state object passed between all nodes.

The state MUST contain the following keys with the specified types:

| Key | Type | Description |
|-----|------|-------------|
| `project_id` | `str` | Unique project/thread identifier |
| `loop_index` | `int` | Monotonically increasing loop counter (incremented by `summarize_node`) |
| `loop_goal` | `str` | Current research goal; may be updated by `revise_node` or replan |
| `implementation_plan` | `Optional[dict]` | Structured plan produced by `plan_node` |
| `last_result` | `str` | Routing signal: `"PASS"` / `"FAIL"` / `"TERMINATE"` / `"UNKNOWN"` |
| `last_reason` | `str` | Human-readable explanation of `last_result` |
| `loop_count_since_review` | `int` | PASS loops since last human review; reset to 0 after `notify_planka` |
| `last_checkpoint_decision` | `Optional[dict]` | Most recent human decision dict (action, notes, etc.) |
| `needs_human_approval` | `bool` | Flag set by `plan_node`; triggers interrupt in `implement_node` |
| `attempt_count` | `int` | revise→implement retry counter within a single loop |
| `test_metrics` | `dict` | Plugin-defined metrics from the last test run |
| `artifacts` | `list` | List of artifact refs (`{"type": str, "path": str}`) |

#### Scenario: State is initialised correctly on start
- **WHEN** `cli start` is called with `--project`, `--plugin`, `--goal`
- **THEN** `graph.invoke` receives the initial state with `loop_index=0`, `last_result="UNKNOWN"`, `needs_human_approval=False`, `attempt_count=0`, `test_metrics={}`, `artifacts=[]`

---

### Requirement: Loop-counter router
After `record_metrics`, the system SHALL route to `notify_planka` when `loop_count_since_review >= review_interval`, otherwise route to `plan`.

`review_interval` SHALL default to `plugin.get_review_interval()` (default: 5) and MAY be overridden via `config["review_interval"]` or `--review-interval` CLI flag.

#### Scenario: Review threshold reached
- **WHEN** `loop_count_since_review` equals `review_interval`
- **THEN** the graph routes to `notify_planka` (which calls `interrupt()`)

#### Scenario: Below threshold
- **WHEN** `loop_count_since_review` is less than `review_interval`
- **THEN** the graph routes directly back to `plan` for the next loop

---

### Requirement: After-review router
After `notify_planka` resumes, the system SHALL route based on `last_checkpoint_decision.action`:
- `"continue"` → `implement` (resume mid-loop)
- `"replan"` → `plan` (new loop with updated goal)
- `"terminate"` → `END`

#### Scenario: Operator chooses replan
- **WHEN** `notify_planka` interrupt is resumed with `{"action": "replan", "notes": "use ATR filter"}`
- **THEN** the graph routes to `plan` with `last_checkpoint_decision` containing the notes

#### Scenario: Operator terminates
- **WHEN** `notify_planka` interrupt is resumed with `{"action": "terminate"}`
- **THEN** the graph routes to `END`

---

### Requirement: Graph cache
The system SHALL cache compiled graph instances in-process, keyed by plugin name, via `get_or_build_graph(plugin, config)`.

#### Scenario: Second call returns cached graph
- **WHEN** `get_or_build_graph` is called twice with the same plugin name
- **THEN** `build_graph` is only invoked once; the second call returns the cached instance

#### Scenario: Cache does not persist across restarts
- **WHEN** the process restarts
- **THEN** the graph is rebuilt from scratch; all durable state is restored from `PostgresSaver`