## 1. Verify Plugin Interface spec against source

- [ ] 1.1 Confirm `ResearchPlugin` ABC defines exactly six abstract methods matching spec names (plan_node, implement_node, test_node, analyze_node, revise_node, summarize_node)
- [ ] 1.2 Confirm `@register` decorator raises `ValueError` on duplicate plugin name
- [ ] 1.3 Confirm `discover_plugins()` scans `projects/*/plugin.py`, is idempotent, and logs warnings on import failure
- [ ] 1.4 Confirm `get_review_interval()` default is 5 and `config["review_interval"]` takes precedence
- [ ] 1.5 Confirm framework-owned state keys (`project_id`, `loop_count_since_review`, `last_checkpoint_decision`, `artifacts`) are not overwritten by plugin nodes

## 2. Verify Graph Workflow spec against source

- [ ] 2.1 Confirm `ResearchState` TypedDict contains all 12 keys listed in spec (project_id, loop_index, loop_goal, implementation_plan, last_result, last_reason, loop_count_since_review, last_checkpoint_decision, needs_human_approval, attempt_count, test_metrics, artifacts)
- [ ] 2.2 Confirm `build_graph()` adds exactly nine nodes (plan, implement, test, analyze, revise, summarize, record_metrics, record_terminate_metrics, notify_planka)
- [ ] 2.3 Confirm fixed edges match spec: START→plan, plan→implement, implement→test, test→analyze, revise→implement, summarize→record_metrics, record_terminate_metrics→END
- [ ] 2.4 Confirm `_analyze_router` routes PASS→summarize, FAIL→revise, TERMINATE→record_terminate_metrics
- [ ] 2.5 Confirm `_make_loop_counter_router` compares `loop_count_since_review >= review_interval` and routes to notify_planka or plan
- [ ] 2.6 Confirm `notify_planka` routes continue→implement, replan→plan, terminate→END
- [ ] 2.7 Confirm `record_metrics` uses `loop_index - 1` (post-summarize) and `record_terminate_metrics` uses `loop_index` as-is
- [ ] 2.8 Confirm `PostgresSaver` is used as checkpointer and `setup()` is called on graph build
- [ ] 2.9 Confirm `get_or_build_graph` caches by `plugin.name` and returns same instance on second call

## 3. Verify Database Schema spec against source

- [ ] 3.1 Confirm `001_business_schema.sql` DDL matches spec for `projects` (id, name, plugin_name, goal, config, created_at)
- [ ] 3.2 Confirm `001_business_schema.sql` DDL matches spec for `loop_metrics` (all columns, UNIQUE constraint, ON CONFLICT UPDATE clause in queries.py)
- [ ] 3.3 Confirm `001_business_schema.sql` DDL matches spec for `checkpoint_decisions` (all columns, no unique constraint)
- [ ] 3.4 Confirm `get_connection()` is a context manager using `psycopg_pool.ConnectionPool` with min=1, max=5, autocommit=True
- [ ] 3.5 Confirm `create_project()` uses `ON CONFLICT (id) DO NOTHING`
- [ ] 3.6 Confirm `record_loop_metrics()` uses `ON CONFLICT (project_id, loop_index) DO UPDATE SET`
- [ ] 3.7 Confirm `_maybe_run_migration()` is called by CLI `start` command and logs warning on failure without aborting

## 4. Verify REST API spec against source

- [ ] 4.1 Confirm `GET /health` returns `{"status": "ok"}` with HTTP 200
- [ ] 4.2 Confirm `POST /resume` accepts `{project_id, decision}`, resumes graph, writes checkpoint_decision, returns `{"status": "resumed", "project_id": ...}`
- [ ] 4.3 Confirm `POST /resume` returns HTTP 404 when project_id is not in `projects` table
- [ ] 4.4 Confirm `POST /planka-webhook` maps "Approved" → `action="continue"` and "Rejected" → `action="terminate"`
- [ ] 4.5 Confirm `POST /planka-webhook` returns `{"status": "ignored"}` for unrecognised list names
- [ ] 4.6 Confirm `POST /planka-webhook` returns `{"status": "error"}` when `thread_id` is missing from card description
- [ ] 4.7 Confirm `main.py` calls `discover_plugins()` before importing the FastAPI `app`

## 5. Verify CLI Commands spec against source

- [ ] 5.1 Confirm `start` command signature: --project (required), --plugin (default "dummy"), --goal (default "default research goal"), --review-interval (default 0)
- [ ] 5.2 Confirm `start` uses the 12-key initial state dict matching spec
- [ ] 5.3 Confirm `status` command prints loop_index, loop_goal, last_result, loop_count_since_review, last_checkpoint_decision, next_nodes, interrupt payload (if any), and loop history
- [ ] 5.4 Confirm `approve` command signature: --project (required), --action (required), --notes, --reason; handles "no pending interrupt" gracefully with exit 0
- [ ] 5.5 Confirm `plugins` command lists all registered plugins and prints "No plugins registered." when registry is empty
- [ ] 5.6 Confirm CLI calls `discover_plugins()` at startup before any command runs
