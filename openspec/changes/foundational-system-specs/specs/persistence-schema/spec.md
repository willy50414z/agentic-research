## ADDED Requirements

### Requirement: LangGraph checkpoint tables
The system SHALL use `langgraph.checkpoint.postgres.PostgresSaver` as the graph checkpointer. `PostgresSaver.setup()` SHALL be called once at graph compile time to create LangGraph's internal tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_migrations`) if they do not already exist.

These tables are managed exclusively by LangGraph and SHALL NOT be written to directly by application code.

#### Scenario: Checkpoint tables auto-created
- **WHEN** `build_graph` is called for the first time against an empty database
- **THEN** `checkpointer.setup()` creates the three LangGraph tables without error

---

### Requirement: projects table
The system SHALL maintain a `projects` table as the registry of all research projects.

```sql
CREATE TABLE IF NOT EXISTS projects (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    plugin_name VARCHAR(100) NOT NULL,
    goal        TEXT         NOT NULL,
    config      JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

`id` serves as the LangGraph `thread_id`. A project record MUST be created before `graph.invoke` is called.

#### Scenario: Project created before graph start
- **WHEN** `cli start` is called
- **THEN** `create_project` inserts a row into `projects` before `graph.invoke` is called

#### Scenario: Duplicate project ID is a no-op
- **WHEN** `create_project` is called with an `id` that already exists
- **THEN** the INSERT is silently ignored (`ON CONFLICT (id) DO NOTHING`) and no error is raised

---

### Requirement: loop_metrics table
The system SHALL record one row in `loop_metrics` for every PASS loop, written by the framework's `record_metrics` node (not by plugins directly).

```sql
CREATE TABLE IF NOT EXISTS loop_metrics (
    id               BIGSERIAL   PRIMARY KEY,
    project_id       VARCHAR(100) NOT NULL REFERENCES projects(id),
    loop_index       INTEGER      NOT NULL,
    win_rate         NUMERIC(5,4),
    alpha_ratio      NUMERIC(8,4),
    max_drawdown     NUMERIC(8,4),
    is_profit_factor NUMERIC(8,4),
    oos_profit_factor NUMERIC(8,4),
    result           VARCHAR(20)  NOT NULL,
    reason           TEXT,
    report_minio_key TEXT,
    recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, loop_index)
);
```

The domain-specific metric columns (`win_rate`, `alpha_ratio`, etc.) are nullable. Plugins populate them via `state["test_metrics"]` using matching key names.

`report_minio_key` stores a local artifact path or future MinIO object key.

#### Scenario: PASS loop recorded automatically
- **WHEN** `record_metrics_node` executes after `summarize_node`
- **THEN** a row is inserted with `result="PASS"`, `loop_index = state["loop_index"] - 1`, and any matching keys from `state["test_metrics"]`

#### Scenario: Duplicate loop index is upserted
- **WHEN** `record_metrics_node` tries to insert a row with the same `(project_id, loop_index)` pair
- **THEN** the existing row is updated (`ON CONFLICT DO UPDATE`) with the new result, reason, report_path, and `recorded_at`

---

### Requirement: checkpoint_decisions table
The system SHALL record every human HITL decision in `checkpoint_decisions` as an immutable audit trail.

```sql
CREATE TABLE IF NOT EXISTS checkpoint_decisions (
    id            BIGSERIAL    PRIMARY KEY,
    project_id    VARCHAR(100) NOT NULL REFERENCES projects(id),
    loop_index    INTEGER,
    action        VARCHAR(50)  NOT NULL,
    notes         TEXT,
    modified_plan JSONB,
    decided_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

Valid `action` values: `continue`, `replan`, `terminate`, `approve`, `reject`.

Decisions SHALL be written by the CLI `approve` command and the `/resume` API endpoint after `graph.invoke` returns.

#### Scenario: Approve decision recorded
- **WHEN** `cli approve --action approve` completes
- **THEN** a row is inserted with `action="approve"` and the current `loop_index`

#### Scenario: Replan notes captured
- **WHEN** `cli approve --action replan --notes "switch to ATR"` completes
- **THEN** the row has `action="replan"` and `notes="switch to ATR"`

---

### Requirement: Business schema migration
The system SHALL apply `db/migrations/001_business_schema.sql` automatically when `cli start` is first called, using `framework.db.connection.run_migration`. Subsequent calls SHALL be idempotent (all DDL uses `CREATE TABLE IF NOT EXISTS`).

#### Scenario: Migration idempotent on second run
- **WHEN** `cli start` is called on a database that already has all tables
- **THEN** migration completes without error (no table dropped or recreated)