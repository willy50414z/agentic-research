-- 001_business_schema.sql
-- Business tables for the agentic research workflow.
-- LangGraph checkpointer tables are created automatically by PostgresSaver.setup().

-- ---------------------------------------------------------------------------
-- projects
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    plugin_name VARCHAR(100) NOT NULL,
    goal        TEXT         NOT NULL,
    config      JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- loop_metrics — one row per completed loop (PASS or FAIL)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS loop_metrics (
    id               BIGSERIAL   PRIMARY KEY,
    project_id       VARCHAR(100) NOT NULL REFERENCES projects(id),
    loop_index       INTEGER      NOT NULL,
    -- Domain-specific metrics (nullable; plugins populate what they have)
    win_rate         NUMERIC(5,4),
    alpha_ratio      NUMERIC(8,4),
    max_drawdown     NUMERIC(8,4),
    is_profit_factor NUMERIC(8,4),
    oos_profit_factor NUMERIC(8,4),
    -- Routing result
    result           VARCHAR(20)  NOT NULL,   -- PASS | FAIL
    reason           TEXT,
    report_minio_key TEXT,                    -- local path or future MinIO key
    recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, loop_index)
);

-- ---------------------------------------------------------------------------
-- checkpoint_decisions — audit trail for human review decisions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkpoint_decisions (
    id            BIGSERIAL    PRIMARY KEY,
    project_id    VARCHAR(100) NOT NULL REFERENCES projects(id),
    loop_index    INTEGER,
    action        VARCHAR(50)  NOT NULL,   -- continue | replan | terminate | approve | reject
    notes         TEXT,
    modified_plan JSONB,
    decided_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
