CREATE SCHEMA IF NOT EXISTS reporting;

CREATE TABLE IF NOT EXISTS reporting.weekly_warehouse_client_performance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse TEXT NOT NULL,
    client_id TEXT NOT NULL,
    week_start DATE NOT NULL,
    inbound_units_count INTEGER NOT NULL DEFAULT 0,
    outbound_orders_count INTEGER NOT NULL DEFAULT 0,
    stockout_events_count INTEGER NOT NULL DEFAULT 0,
    discrepancy_events_count INTEGER NOT NULL DEFAULT 0,
    discrepancy_rate NUMERIC NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_weekly_warehouse_client_performance
        UNIQUE (warehouse, client_id, week_start)
);

CREATE INDEX IF NOT EXISTS ix_weekly_warehouse_client_performance_week_start
    ON reporting.weekly_warehouse_client_performance (week_start);

CREATE TABLE IF NOT EXISTS reporting.pipeline_runs (
    run_id UUID PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    week_start DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed', 'unknown')),
    records_read INTEGER NOT NULL DEFAULT 0 CHECK (records_read >= 0),
    records_transformed INTEGER NOT NULL DEFAULT 0 CHECK (records_transformed >= 0),
    records_written INTEGER NOT NULL DEFAULT 0 CHECK (records_written >= 0),
    error_message TEXT,
    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual', 'late_event')),
    phase TEXT NOT NULL
        CHECK (phase IN ('extracting', 'transforming', 'loading', 'completed')),
    last_checkpoint_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_reporting_pipeline_runs_latest
    ON reporting.pipeline_runs (pipeline_name, started_at DESC);

ALTER TABLE reporting.weekly_warehouse_client_performance
    ENABLE ROW LEVEL SECURITY;

ALTER TABLE reporting.pipeline_runs
    ENABLE ROW LEVEL SECURITY;
