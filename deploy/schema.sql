-- Agentic Research Framework — full schema (drop + create)
-- Run this to reset the database completely. No migrations, no ALTER TABLE.

-- Drop all framework tables (order matters for FK constraints)
DROP TABLE IF EXISTS checkpoint_decisions CASCADE;
DROP TABLE IF EXISTS loop_metrics CASCADE;
DROP TABLE IF EXISTS projects CASCADE;

-- Drop legacy LangGraph checkpoint tables if they exist
DROP TABLE IF EXISTS checkpoints CASCADE;
DROP TABLE IF EXISTS checkpoint_blobs CASCADE;
DROP TABLE IF EXISTS checkpoint_writes CASCADE;
DROP TABLE IF EXISTS checkpoint_migrations CASCADE;

-- ── projects ──────────────────────────────────────────────────────────────────
CREATE TABLE projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    plugin_name   TEXT NOT NULL,
    goal          TEXT NOT NULL DEFAULT '',
    config        JSONB NOT NULL DEFAULT '{}',
    workflow_step VARCHAR(64) DEFAULT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── loop_metrics ──────────────────────────────────────────────────────────────
CREATE TABLE loop_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    loop_index          INT NOT NULL,
    win_rate            FLOAT,
    alpha_ratio         FLOAT,
    max_drawdown        FLOAT,
    is_profit_factor    FLOAT,
    oos_profit_factor   FLOAT,
    result              TEXT NOT NULL,   -- PASS | FAIL | TERMINATE
    reason              TEXT,
    report_minio_key    TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, loop_index)
);

-- ── checkpoint_decisions ──────────────────────────────────────────────────────
CREATE TABLE checkpoint_decisions (
    id             BIGSERIAL PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    loop_index     INT NOT NULL,
    action         TEXT NOT NULL,   -- continue | replan | terminate | approve | reject
    notes          TEXT,
    modified_plan  JSONB,
    decided_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
