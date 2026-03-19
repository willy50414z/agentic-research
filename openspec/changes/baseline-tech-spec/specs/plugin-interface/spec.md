## ADDED Requirements

### Requirement: Plugin class definition
A plugin SHALL be a Python class that inherits from `framework.plugin_interface.ResearchPlugin` (an ABC) and is decorated with `@framework.plugin_registry.register`.

The class SHALL declare a `name` attribute (string, snake_case, unique across all registered plugins) as either a `@property` or a plain class attribute.

#### Scenario: Plugin with duplicate name raises on import
- **WHEN** two plugin classes register with the same `name`
- **THEN** a `ValueError` is raised at import time with the message "Plugin '<name>' is already registered."

#### Scenario: Plugin with unique name registers successfully
- **WHEN** a plugin class with a unique `name` is imported
- **THEN** `framework.plugin_registry.list_plugins()` includes that name

---

### Requirement: Six required node methods
A plugin SHALL implement exactly six node methods. Each method accepts `state: dict` (the current `ResearchState`) and returns a `dict` of state updates (partial update — only keys that change).

The six methods, in execution order, are:
1. `plan_node(state)` — generate implementation plan
2. `implement_node(state)` — execute the plan (may pause for human review)
3. `test_node(state)` — run domain-specific validation
4. `analyze_node(state)` — evaluate results and set routing signal
5. `revise_node(state)` — propose a fix after FAIL
6. `summarize_node(state)` — produce a loop summary after PASS

#### Scenario: Missing node method raises at graph build time
- **WHEN** a plugin class omits any of the six node methods
- **THEN** instantiating the class raises `TypeError` (Python ABC enforcement)

#### Scenario: Node method returns partial state
- **WHEN** a node method returns `{"last_result": "PASS"}`
- **THEN** only `last_result` is updated in the graph state; all other keys retain their previous values

---

### Requirement: plan_node contract
`plan_node` SHALL read `state["loop_goal"]` and `state["last_checkpoint_decision"]` (may be `None`) to generate the plan.

`plan_node` SHALL write `implementation_plan` (a dict, plugin-defined schema) and `needs_human_approval=True` to indicate a plan review interrupt is required.

If `last_checkpoint_decision["action"] == "terminate"`, `plan_node` SHALL write `last_result="TERMINATE"` instead of generating a plan.

#### Scenario: Normal plan generation
- **WHEN** `plan_node` is called with a non-terminate `last_checkpoint_decision`
- **THEN** the returned dict contains `implementation_plan` (non-None) and `needs_human_approval=True`

#### Scenario: Terminate signal propagation
- **WHEN** `plan_node` is called with `last_checkpoint_decision["action"] == "terminate"`
- **THEN** the returned dict contains `last_result="TERMINATE"`

---

### Requirement: implement_node contract
`implement_node` SHALL call `langgraph.types.interrupt(payload)` when `state["needs_human_approval"]` is `True` and SHALL NOT call `interrupt()` otherwise (e.g., on the revise→implement path).

After a Plan Review interrupt is resumed with `{"action": "approve"}`, `implement_node` SHALL write `needs_human_approval=False` and MAY append to `artifacts`.

After a Plan Review interrupt is resumed with `{"action": "reject", "reason": "..."}`, `implement_node` SHALL write `needs_human_approval=False`; the graph will route back to `plan` on the next invocation.

#### Scenario: Plan review interrupt fires on first loop
- **WHEN** `implement_node` is entered with `needs_human_approval=True`
- **THEN** the graph pauses and `graph.get_state().next` contains `["implement"]`

#### Scenario: No interrupt on revise path
- **WHEN** `implement_node` is entered with `needs_human_approval=False` (after `revise`)
- **THEN** execution continues without pause and `graph.get_state().next` does NOT contain `["implement"]`

---

### Requirement: analyze_node contract
`analyze_node` SHALL write exactly two keys: `last_result` (one of `"PASS"`, `"FAIL"`, `"TERMINATE"`) and `last_reason` (a human-readable string explaining the result).

The routing of the graph depends entirely on `last_result`:
- `"PASS"` → `summarize`
- `"FAIL"` → `revise`
- `"TERMINATE"` → `record_terminate_metrics` → END

#### Scenario: PASS routing
- **WHEN** `analyze_node` sets `last_result="PASS"`
- **THEN** the next node executed is `summarize`

#### Scenario: FAIL routing
- **WHEN** `analyze_node` sets `last_result="FAIL"`
- **THEN** the next node executed is `revise`

#### Scenario: TERMINATE routing
- **WHEN** `analyze_node` sets `last_result="TERMINATE"`
- **THEN** the graph reaches END after recording terminate metrics

---

### Requirement: summarize_node contract
`summarize_node` SHALL write:
- `last_reason`: summary text (shown in CLI status and Planka card)
- `loop_index`: incremented by 1
- `loop_count_since_review`: incremented by 1
- `attempt_count`: reset to 0

`summarize_node` MAY append a summary artifact reference to `artifacts` (dict with at least `{"type": "summary", "path": "..."}`).

#### Scenario: Loop index increments after PASS
- **WHEN** `summarize_node` completes with `loop_index=2`
- **THEN** the next `plan_node` call sees `loop_index=3`

#### Scenario: attempt_count resets after PASS
- **WHEN** `summarize_node` completes after a FAIL→revise→PASS sequence with `attempt_count=2`
- **THEN** `attempt_count` is `0` for the next loop

---

### Requirement: get_review_interval override
A plugin MAY override `get_review_interval() -> int` to control how many PASS loops occur between Loop Review checkpoints. The default is `5`.

The framework also accepts `review_interval` in the `config` dict passed to `build_graph()`, which takes precedence over the plugin's default.

#### Scenario: Custom interval from plugin
- **WHEN** a plugin overrides `get_review_interval()` to return `3`
- **THEN** a Loop Review interrupt fires after every 3 PASS loops

#### Scenario: Config override takes precedence
- **WHEN** the plugin returns `5` from `get_review_interval()` but `config["review_interval"] = 2`
- **THEN** Loop Review fires after 2 PASS loops

---

### Requirement: Plugin auto-discovery
`framework.plugin_registry.discover_plugins()` SHALL scan `projects/*/plugin.py` using glob and import each module via `importlib.import_module()`, triggering `@register` decorators.

`discover_plugins()` SHALL be idempotent: if a module is already in `sys.modules`, it SHALL be skipped without error.

`discover_plugins()` SHALL log a warning (not raise) if a plugin module fails to import.

#### Scenario: All plugins discovered on startup
- **WHEN** `discover_plugins()` is called once at application startup
- **THEN** all plugins under `projects/*/plugin.py` appear in `list_plugins()`

#### Scenario: Broken plugin does not block others
- **WHEN** one `projects/*/plugin.py` has a syntax error
- **THEN** `discover_plugins()` logs a warning for that plugin and continues importing the remaining plugins

---

### Requirement: Framework-owned state keys
The following `ResearchState` keys are owned by the framework and SHALL NOT be overwritten by plugin nodes (they are managed by framework nodes or the graph router):
- `project_id` — set once at graph start
- `loop_count_since_review` — managed by `summarize_node` contract and Loop Review
- `last_checkpoint_decision` — set by the graph after interrupt resume
- `artifacts` — list; plugins append but SHALL NOT replace the entire list

#### Scenario: Plugin appends to artifacts without clobbering
- **WHEN** a plugin node returns `{"artifacts": [new_ref]}`
- **THEN** the framework merges the new ref with any existing artifacts in state

#### Scenario: Plugin does not reset loop_count_since_review
- **WHEN** a plugin node returns a dict without `loop_count_since_review`
- **THEN** the framework counter continues from its previous value
