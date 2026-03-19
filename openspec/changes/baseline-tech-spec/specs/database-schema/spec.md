## ADDED Requirements

### Requirement: LangGraph checkpoint tables
LangGraph's `PostgresSaver.setup()` SHALL automatically create three internal tables on first connection:
- `checkpoints`
- `checkpoint_blobs`
- `checkpoint_migrations`

These tables are managed entirely by LangGraph and SHALL NOT be modified by application migrations.

#### Scenario: Tables created on first startup
- **WHEN** `PostgresSaver(conn).setup()` is called on a fresh database
- **THEN** `checkpoints`, `checkpoint_blobs`, and `checkpoint_migrations` tables exist

#### Scenario: setup() is idempotent
- **WHEN** `PostgresSaver(conn).setup()` is called on a database that already has checkpoint tables
- **THEN** no error is raised and existing data is preserved

---

### Requirement: projects table
The `projects` table SHALL store one row per research project (one per LangGraph thread).

Schema:
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

- `id` is the project identifier and equals the LangGraph `thread_id`
- `plugin_name` references the registered plugin name (no FK constraint; validated in application layer)
- `config` stores plugin-specific overrides (e.g., `review_interval`)
- Inserting a duplicate `id` SHALL be silently ignored (`ON CONFLICT DO NOTHING`)

#### Scenario: Project created with unique ID
- **WHEN** `create_project(project_id="qa_001", ...)` is called
- **THEN** a row with `id="qa_001"` exists in `projects`

#### Scenario: Duplicate project ID is ignored
- **WHEN** `create_project(project_id="qa_001", ...)` is called twice
- **THEN** only one row exists and no error is raised

---

### Requirement: loop_metrics table
The `loop_metrics` table SHALL store one row per completed loop (PASS, FAIL, or TERMINATE).

Schema:
```sql
CREATE TABLE IF NOT EXISTS loop_metrics (
    id                BIGSERIAL    PRIMARY KEY,
    project_id        VARCHAR(100) NOT NULL REFERENCES projects(id),
    loop_index        INTEGER      NOT NULL,
    win_rate          NUMERIC(5,4),
    alpha_ratio       NUMERIC(8,4),
    max_drawdown      NUMERIC(8,4),
    is_profit_factor  NUMERIC(8,4),
    oos_profit_factor NUMERIC(8,4),
    result            VARCHAR(20)  NOT NULL,
    reason            TEXT,
    report_minio_key  TEXT,
    recorded_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, loop_index)
);
```

- `result` SHALL be one of: `"PASS"`, `"FAIL"`, `"TERMINATE"`
- Domain metric columns (`win_rate`, `alpha_ratio`, etc.) are nullable; plugins populate only the keys they produce
- `report_minio_key` stores a local artifact path or a future MinIO object key; may be NULL
- The `UNIQUE (project_id, loop_index)` constraint ensures upsert safety: a second write for the same loop updates `result`, `reason`, `report_minio_key`, and `recorded_at`
- The framework writes this table via `record_metrics` (PASS) and `record_terminate_metrics` (TERMINATE) framework nodes; plugins do NOT write directly

#### Scenario: PASS loop metrics written
- **WHEN** a loop completes with `last_result="PASS"` and `test_metrics={"win_rate": 0.60}`
- **THEN** `loop_metrics` contains a row with `result="PASS"` and `win_rate=0.60`

#### Scenario: TERMINATE loop metrics written
- **WHEN** a loop terminates early with `last_result="TERMINATE"`
- **THEN** `loop_metrics` contains a row with `result="TERMINATE"` and nullable domain metrics

#### Scenario: Duplicate write updates existing row
- **WHEN** `record_loop_metrics` is called twice for the same `(project_id, loop_index)`
- **THEN** only one row exists with the values from the second call

---

### Requirement: checkpoint_decisions table
The `checkpoint_decisions` table SHALL store one row per human review decision (Plan Review or Loop Review).

Schema:
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

- `action` SHALL be one of: `"approve"`, `"reject"`, `"continue"`, `"replan"`, `"terminate"`
- `notes` stores free-text reviewer guidance (e.g., replan direction)
- `modified_plan` stores a JSON object with additional decision context (e.g., `{"reason": "..."}` for reject)
- Multiple decisions per `(project_id, loop_index)` are permitted (no unique constraint)
- This table is written by the CLI `approve` command and the `/resume` API endpoint; it is NOT written by framework graph nodes

#### Scenario: Approve decision recorded
- **WHEN** `approve --project qa_001 --action approve` is run
- **THEN** a row with `action="approve"` exists in `checkpoint_decisions` for `project_id="qa_001"`

#### Scenario: Replan with notes recorded
- **WHEN** `approve --project qa_001 --action replan --notes "use ATR filter"`
- **THEN** a row exists with `action="replan"` and `notes="use ATR filter"`

---

### Requirement: Database connection
The application SHALL connect to PostgreSQL using a `psycopg_pool.ConnectionPool` with:
- `min_size=1`, `max_size=5`
- `autocommit=True`
- Connection string read from `DATABASE_URL` environment variable

The pool SHALL be initialised once per process (lazy singleton) and shared across all DB operations.

`framework.db.connection.get_connection()` SHALL be used as a context manager (`with get_connection() as conn:`) and SHALL NOT be called outside a `with` block.

#### Scenario: Connection pool initialises on first use
- **WHEN** any DB query function is called for the first time
- **THEN** the pool is created and a connection is acquired

#### Scenario: Concurrent queries use pool
- **WHEN** two threads call `get_connection()` simultaneously
- **THEN** each gets a separate connection from the pool (up to max_size)

---

### Requirement: Business schema migration
The file `db/migrations/001_business_schema.sql` SHALL be applied before any business table operations. The CLI `start` command SHALL attempt to apply this migration via `run_migration()`.

`run_migration()` SHALL use `CREATE TABLE IF NOT EXISTS` so repeated runs are idempotent.

Failed migration (e.g., already applied) SHALL be logged as a warning and SHALL NOT abort the CLI command.

#### Scenario: Fresh database initialised by start command
- **WHEN** `cli/main.py start ...` is run against a fresh database
- **THEN** `projects`, `loop_metrics`, and `checkpoint_decisions` tables exist after the command

#### Scenario: Repeated migration is safe
- **WHEN** `run_migration()` is called on a database that already has the business tables
- **THEN** no error is raised and no data is lost
