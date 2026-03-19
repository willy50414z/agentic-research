## ADDED Requirements

### Requirement: start command
`python cli/main.py start` SHALL create a new project record in PostgreSQL, apply the business schema migration if needed, and invoke the graph until the first interrupt (plan review).

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--project` / `-p` | Yes | — | Project ID (used as LangGraph `thread_id`) |
| `--plugin` | No | `dummy` | Plugin name to use |
| `--goal` / `-g` | No | `"default research goal"` | Research goal text |
| `--review-interval` | No | `0` (use plugin default) | Override PASS loop count between reviews |

The command SHALL print project info and then call `_print_graph_state` to show the paused state after the first interrupt.

#### Scenario: Start creates project and pauses at plan review
- **WHEN** `python cli/main.py start --project p1 --plugin dummy --goal "find alpha"` is run
- **THEN** a row is inserted into `projects`, the graph runs until `implement_node` calls `interrupt()`, and the CLI prints `[PAUSED] Run \`approve\` to resume.`

#### Scenario: Missing DATABASE_URL exits with error
- **WHEN** `start` is called and `DATABASE_URL` is not set
- **THEN** the command prints `[ERROR] DATABASE_URL is not set.` to stderr and exits with code 1

---

### Requirement: status command
`python cli/main.py status` SHALL display the current LangGraph state snapshot and the loop history from `loop_metrics`.

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--project` / `-p` | Yes | — | Project ID |

**Output format:**
```
--- Project: <project_id> ---
  loop_index              : <int>
  loop_goal               : <string, truncated to 80 chars>
  last_result             : <string>
  loop_count_since_review : <int>
  last_checkpoint_decision: <dict or None>
  next_nodes              : <list>

[INTERRUPT] Waiting for human input:
  checkpoint: <type>
  ...

--- Loop History ---
  Loop  0: PASS   win_rate=0.5500  alpha=1.2300  reason: ...
```

#### Scenario: Status shows interrupt payload
- **WHEN** the graph is paused at `notify_planka_node`
- **THEN** `status` prints `[INTERRUPT] Waiting for human input:` with `checkpoint: loop_review`

#### Scenario: Status shows DONE when graph completed
- **WHEN** the graph has reached `END`
- **THEN** `status` prints `[DONE] Graph has completed (reached END).`

---

### Requirement: approve command
`python cli/main.py approve` SHALL resume a paused graph with a human decision and record the decision in `checkpoint_decisions`.

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--project` / `-p` | Yes | — | Project ID |
| `--action` / `-a` | Yes | — | Decision: `approve` / `reject` / `continue` / `replan` / `terminate` |
| `--notes` / `-n` | No | `""` | Optional notes (used with `replan`) |
| `--reason` / `-r` | No | `""` | Rejection reason (used with `reject`) |

If the project has no pending interrupt, the command SHALL print `[INFO] ... no pending interrupt.` and exit with code 0.

After resuming, the command SHALL call `_print_graph_state` to show the updated state.

#### Scenario: Approve plan resumes implement
- **WHEN** `approve --project p1 --action approve` is called
- **THEN** `graph.invoke(Command(resume={"action": "approve"}), ...)` is called, a `checkpoint_decisions` row is inserted, and the updated state is printed

#### Scenario: No interrupt is a no-op
- **WHEN** `approve` is called on a project with no pending interrupt (`state.next` is empty)
- **THEN** the command prints `[INFO] ... has no pending interrupt.` and exits 0 without modifying state

#### Scenario: Terminate ends the graph
- **WHEN** `approve --project p1 --action terminate` is called during a loop-review interrupt
- **THEN** the graph routes to `END` and status prints `[DONE] Graph has completed.`

---

### Requirement: plugins command
`python cli/main.py plugins` SHALL list all currently registered plugin names, one per line, prefixed with `  - `.

If no plugins are registered, it SHALL print `No plugins registered.`

#### Scenario: Lists registered plugins
- **WHEN** `python cli/main.py plugins` is run after importing `projects.dummy.plugin`
- **THEN** output includes `  - dummy`

---

### Requirement: Graph state display helper
All commands that modify or query graph state SHALL call `_print_graph_state(project_id, graph)` after their primary action to display the current state snapshot.

The helper SHALL:
- Print all top-level `ResearchState` fields
- For any task with a non-empty `interrupts` list, print the interrupt payload (excluding the `instruction` key from the main fields block, displaying it as a separate line below)

#### Scenario: Interrupt instruction displayed separately
- **WHEN** a task has an interrupt with `"instruction"` key
- **THEN** other keys are printed first as `  key: value`, and `instruction` text is printed on its own line at the end of the interrupt block