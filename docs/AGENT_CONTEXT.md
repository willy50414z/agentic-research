# Agentic Research Workflow Engine — System Context

> Paste this file into your agent's system prompt or context window to give it a working understanding of the system. For full plugin development details see `docs/PLUGIN_SPEC.md`.

---

## What This System Is

A **domain-agnostic research loop engine** built on LangGraph. It runs iterative experiments, pauses for human review at two configurable checkpoints, persists all state in PostgreSQL, and optionally logs metrics to MLflow and posts review cards to Planka.

The framework owns zero business logic. All domain behaviour lives in **plugins** (`projects/<name>/plugin.py`).

---

## Architecture at a Glance

```
START → plan → implement* → test → analyze
                                      │ FAIL  → revise → implement (no interrupt)
                                      │ PASS  → summarize → record_metrics
                                      │                          │ every N loops → Loop Review ⏸
                                      │                          │ continue → plan
                                      │ TERMINATE → record_terminate_metrics → END
```

`*` implement pauses for **Plan Review** (`needs_human_approval=True`)

### Key Files

| Path | Purpose |
|------|---------|
| `framework/graph.py` | Builds LangGraph graph; `ResearchState` TypedDict |
| `framework/plugin_interface.py` | `ResearchPlugin` ABC — the plugin contract |
| `framework/plugin_registry.py` | `@register` decorator; `discover_plugins()` |
| `framework/notify.py` | `notify_planka_node` — Loop Review interrupt |
| `framework/db/queries.py` | `create_project`, `record_loop_metrics`, `record_checkpoint_decision` |
| `framework/api/server.py` | FastAPI: `POST /resume`, `POST /planka-webhook`, `GET /health` |
| `cli/main.py` | Typer CLI: `start`, `status`, `approve`, `plugins` |
| `projects/sample/plugin.py` | **Reference implementation** — read this first |
| `projects/dummy/plugin.py` | Minimal plugin (fixed FAIL→PASS logic) |

### Services (Docker Compose)

| Service | Port | Purpose |
|---------|------|---------|
| `postgres` | 5432 | LangGraph checkpoint store + business schema |
| `langgraph-engine` | 7001 | FastAPI + workflow engine |
| `mlflow` | 5000 | Experiment tracking UI (optional) |
| `planka` | 7002 | Kanban HITL board (optional) |

---

## ResearchState — Full Schema

```python
{
  "project_id":               str,           # LangGraph thread_id
  "loop_index":               int,           # 0-based, incremented by summarize_node
  "loop_goal":                str,           # research objective, max 500 chars
  "implementation_plan":      dict | None,   # plugin-defined
  "last_result":              str,           # "PASS"|"FAIL"|"TERMINATE"|"UNKNOWN"
  "last_reason":              str,           # human-readable explanation
  "loop_count_since_review":  int,           # resets to 0 after Loop Review
  "last_checkpoint_decision": dict | None,   # last human decision
  "needs_human_approval":     bool,          # set True by plan_node
  "attempt_count":            int,           # revise iterations within one loop
  "test_metrics":             dict,          # plugin-defined metrics
  "artifacts":                list,          # refs: [{"type":"summary","path":"..."}]
}
```

---

## Human Interaction Points

### Plan Review (after every `plan_node`)
The graph pauses inside `implement_node` when `needs_human_approval=True`.

```bash
# Approve — continue to test
python cli/main.py approve --project <id> --action approve

# Reject — graph terminates
python cli/main.py approve --project <id> --action reject --reason "reason"
```

### Loop Review (every N PASS loops, N = plugin's `get_review_interval()`)
The graph pauses inside `notify_planka_node`.

```bash
# Continue — next loop
python cli/main.py approve --project <id> --action continue

# Replan — inject new goal direction, restart planning
python cli/main.py approve --project <id> --action replan --notes "try lower LR"

# Terminate — end the research
python cli/main.py approve --project <id> --action terminate
```

---

## Database Tables

```
projects             — one row per project (id = thread_id)
loop_metrics         — one row per completed loop (PASS/FAIL/TERMINATE)
checkpoint_decisions — audit trail for every human decision
checkpoints          — LangGraph internal (auto-managed)
```

---

## Quick Commands

```bash
# Start a project (runs until first Plan Review pause)
docker exec agentic-langgraph python cli/main.py start \
  --project my_run --plugin sample --goal "find best config" --review-interval 2

# See current state + loop history
docker exec agentic-langgraph python cli/main.py status --project my_run

# List registered plugins
docker exec agentic-langgraph python cli/main.py plugins

# Resume via HTTP API
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"my_run","decision":{"action":"approve"}}'
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | psycopg3 connection string |
| `MLFLOW_TRACKING_URI` | No | — | e.g. `http://mlflow:5000` |
| `PLANKA_TOKEN` | No | — | Enables Planka card creation |
| `PLANKA_API_URL` | No | — | Planka service URL |
| `PLANKA_REVIEW_LIST_ID` | No | — | Planka list ID for review cards |
| `ARTIFACTS_DIR` | No | `./artifacts` | Local artifact storage path |

---

## Routing Signal Contract

A plugin's `analyze_node` MUST return exactly:
```python
{"last_result": "PASS" | "FAIL" | "TERMINATE", "last_reason": "..."}
```
- `PASS` → summarize → record_metrics → (Loop Review?) → plan
- `FAIL` → revise → implement (no interrupt)
- `TERMINATE` → record_terminate_metrics → END
