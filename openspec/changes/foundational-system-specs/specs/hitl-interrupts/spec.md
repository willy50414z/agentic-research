## ADDED Requirements

### Requirement: Plan Review interrupt
The system SHALL pause graph execution after `plan_node` completes and before `implement_node` continues, when `state["needs_human_approval"] == True`.

The interrupt is implemented by calling `langgraph.types.interrupt(payload)` inside `implement_node`. The interrupt payload SHALL be a dict containing at minimum `checkpoint: "plan_review"`, `loop_index`, and `plan` (the implementation plan dict). Including `project_id` in the payload is RECOMMENDED for traceability.

#### Scenario: Graph pauses awaiting plan approval
- **WHEN** `plan_node` returns `needs_human_approval=True`
- **THEN** `implement_node` calls `interrupt()` and graph execution is suspended; `graph.get_state().next` contains `["implement"]`

#### Scenario: Approve resumes implement
- **WHEN** `graph.invoke(Command(resume={"action": "approve"}), config)` is called
- **THEN** `implement_node` continues past the interrupt and executes the implementation

#### Scenario: Reject signals termination via implement_node
- **WHEN** `graph.invoke(Command(resume={"action": "reject", "reason": "plan incomplete"}), config)` is called
- **THEN** `implement_node` returns `{"last_result": "TERMINATE", "last_reason": "<reason>", "needs_human_approval": False}`; graph continues to `test → analyze`; for termination to reach `END`, the plugin's `analyze_node` MUST propagate `TERMINATE` when it detects `state["last_result"] == "TERMINATE"` on entry

---

### Requirement: Loop Review interrupt
The system SHALL pause graph execution inside `notify_planka_node` after every N PASS loops (where N = `review_interval`).

The interrupt payload SHALL be a dict containing `checkpoint: "loop_review"`, `project_id`, `loop_index`, `summary` (text), and `instruction` (human-readable resume guide).

#### Scenario: Graph pauses for loop review
- **WHEN** `loop_count_since_review >= review_interval`
- **THEN** the graph routes to `notify_planka_node` which calls `interrupt()`, suspending execution

#### Scenario: Continue resumes mid-loop
- **WHEN** resumed with `{"action": "continue"}`
- **THEN** the graph routes to `implement` (continuing the current loop from where it left off)

#### Scenario: Replan routes back to plan
- **WHEN** resumed with `{"action": "replan", "notes": "use ATR filter"}`
- **THEN** `last_checkpoint_decision` is set to the resume dict and the graph routes to `plan_node`

#### Scenario: Terminate exits graph
- **WHEN** resumed with `{"action": "terminate"}`
- **THEN** the graph routes to `END` and `graph.get_state().next` is empty

---

### Requirement: Resume command shape
The system SHALL accept resume decisions via `graph.invoke(Command(resume=decision), config)` where `config = {"configurable": {"thread_id": project_id}}`.

The `decision` dict SHALL conform to one of the following shapes:

| Interrupt type | Valid actions | Optional fields |
|----------------|--------------|----------------|
| Plan Review | `approve`, `reject` | `reason` (string, for reject) |
| Loop Review | `continue`, `replan`, `terminate` | `notes` (string, for replan) |

#### Scenario: Resume with unknown action
- **WHEN** resumed with `{"action": "unknown_action"}`
- **THEN** the `_after_review_router` defaults to `"continue"` (action not in `["terminate", "replan"]`)

---

### Requirement: Loop counter reset after review
After `notify_planka_node` completes (regardless of the action taken), `loop_count_since_review` SHALL be reset to `0`.

#### Scenario: Counter resets after loop review
- **WHEN** `notify_planka_node` returns its state update
- **THEN** the returned dict contains `loop_count_since_review: 0`

---

### Requirement: Planka notification is optional
`notify_planka_node` SHALL create a Planka card only when `PLANKA_API_URL` and `PLANKA_TOKEN` environment variables are both set. When not set, the node SHALL log a warning and proceed directly to `interrupt()`.

#### Scenario: No Planka config — node still interrupts
- **WHEN** `PLANKA_API_URL` is not set
- **THEN** the node skips card creation, logs `"Planka not configured"`, and still calls `interrupt()` to pause for human review