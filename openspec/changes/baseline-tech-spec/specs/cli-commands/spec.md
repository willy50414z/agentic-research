## ADDED Requirements

### Requirement: CLI entry point
The CLI SHALL be implemented using Typer and defined in `cli/main.py`. It SHALL be invokable as:

```
python cli/main.py <command> [options]
```

The CLI SHALL call `discover_plugins()` at startup before any command runs, so all plugins under `projects/*/plugin.py` are registered.

The CLI SHALL load `DATABASE_URL` from the `.env` file at the repo root (via `python-dotenv`). If `DATABASE_URL` is not set, commands that require a database connection SHALL exit with error code 1 and print `[ERROR] DATABASE_URL is not set.`

#### Scenario: CLI entry point accessible
- **WHEN** `python cli/main.py --help` is run
- **THEN** exit code is 0 and help text lists: start, status, approve, plugins

#### Scenario: Missing DATABASE_URL aborts start
- **WHEN** `DATABASE_URL` is unset and `python cli/main.py start ...` is run
- **THEN** exit code is 1 and stderr contains `[ERROR] DATABASE_URL is not set.`

---

### Requirement: start command
`start` SHALL create a new project and run the graph until the first Plan Review interrupt.

**Signature**:
```
python cli/main.py start --project <id> [--plugin <name>] [--goal <text>] [--review-interval <n>]
```

**Options**:
| Option | Default | Description |
|--------|---------|-------------|
| `--project` / `-p` | *(required)* | Project ID (used as LangGraph thread_id) |
| `--plugin` | `"dummy"` | Registered plugin name |
| `--goal` / `-g` | `"default research goal"` | High-level research objective |
| `--review-interval` | `0` (use plugin default) | Override Loop Review interval |

**Behaviour**:
1. Validate `DATABASE_URL`
2. Apply business schema migration (idempotent)
3. Insert row in `projects` table (`ON CONFLICT DO NOTHING`)
4. Build and invoke the graph with the initial state
5. Print the graph state summary on completion/interrupt

**Initial state** used by `start`:
```python
{
    "project_id": project,
    "loop_index": 0,
    "loop_goal": goal,
    "implementation_plan": None,
    "last_result": "UNKNOWN",
    "last_reason": "",
    "loop_count_since_review": 0,
    "last_checkpoint_decision": None,
    "needs_human_approval": False,
    "attempt_count": 0,
    "test_metrics": {},
    "artifacts": [],
}
```

#### Scenario: Start runs to first interrupt
- **WHEN** `start --project qa_001 --plugin quant_alpha --goal "find alpha"`
- **THEN** the graph runs until the Plan Review interrupt and prints `[PAUSED] Run approve to resume.`

#### Scenario: Start with custom review interval
- **WHEN** `start --project qa_001 --review-interval 2`
- **THEN** Loop Review fires after every 2 PASS loops instead of the plugin default

---

### Requirement: status command
`status` SHALL display the current graph state and loop history for a project.

**Signature**:
```
python cli/main.py status --project <id>
```

**Output** (printed to stdout):
```
--- Project: <id> ---
  loop_index          : <n>
  loop_goal           : <text, truncated at 80 chars>
  last_result         : <PASS|FAIL|TERMINATE|UNKNOWN>
  loop_count_since_review: <n>
  last_checkpoint_decision: <dict or None>
  next_nodes          : <list>

[INTERRUPT] Waiting for human input:        ← if paused
  <interrupt payload fields>
  <instruction>

--- Loop History ---                         ← if any metrics exist
  Loop  0: PASS   win_rate=0.6000  alpha=1.1448  reason: ...
```

If the graph has completed (no next nodes), the output SHALL end with `[DONE] Graph has completed (reached END).`

If the graph is paused, the output SHALL end with `[PAUSED] Run approve to resume.`

#### Scenario: Status shows interrupt payload when paused
- **WHEN** the graph is waiting at a Plan Review interrupt
- **THEN** `status` prints `[INTERRUPT] Waiting for human input:` with the plan details

#### Scenario: Status shows loop history
- **WHEN** two PASS loops have completed
- **THEN** `status` prints `--- Loop History ---` with two rows

---

### Requirement: approve command
`approve` SHALL resume a paused graph with a human decision and print the updated state.

**Signature**:
```
python cli/main.py approve --project <id> --action <action> [--notes <text>] [--reason <text>]
```

**Options**:
| Option | Description |
|--------|-------------|
| `--project` / `-p` | *(required)* Project ID |
| `--action` / `-a` | *(required)* Decision: `approve`, `reject`, `continue`, `replan`, `terminate` |
| `--notes` / `-n` | Optional reviewer notes (used with `replan`) |
| `--reason` / `-r` | Optional rejection reason (used with `reject`) |

**Behaviour**:
1. Load project and resolve plugin
2. Check if a pending interrupt exists; if none, print `[INFO] Project has no pending interrupt.` and exit 0
3. Construct decision dict: `{"action": action, "notes": notes, "reason": reason}` (omitting empty values)
4. Resume graph with `Command(resume=decision)`
5. Write a row to `checkpoint_decisions`
6. Print the updated graph state

#### Scenario: Approve resumes Plan Review
- **WHEN** `approve --project qa_001 --action approve`
- **THEN** the graph resumes, execution continues, and the updated state is printed

#### Scenario: No pending interrupt is handled gracefully
- **WHEN** `approve --project qa_001 --action approve` on a project with no active interrupt
- **THEN** exit code is 0 and output contains `[INFO] Project 'qa_001' has no pending interrupt.`

#### Scenario: Replan with notes
- **WHEN** `approve --project qa_001 --action replan --notes "use ATR filter"`
- **THEN** `checkpoint_decisions` row has `action="replan"` and `notes="use ATR filter"`

---

### Requirement: plugins command
`plugins` SHALL list all registered plugin names.

**Signature**:
```
python cli/main.py plugins
```

**Output**:
```
Registered plugins:
  - demo
  - dummy
  - quant_alpha
```

If no plugins are registered, the output SHALL be `No plugins registered.`

#### Scenario: All discovered plugins are listed
- **WHEN** `plugins` is run after startup with three plugins under `projects/`
- **THEN** all three names appear in the output

#### Scenario: Empty registry handled gracefully
- **WHEN** no plugins are registered (no `projects/*/plugin.py` files)
- **THEN** output is `No plugins registered.`
