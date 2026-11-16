ALTER TABLE reporting.pipeline_runs
    ADD COLUMN IF NOT EXISTS prefect_run_id UUID;

ALTER TABLE reporting.pipeline_runs
    DROP CONSTRAINT IF EXISTS pipeline_runs_status_check;

ALTER TABLE reporting.pipeline_runs
    DROP CONSTRAINT IF EXISTS ck_reporting_pipeline_runs_status;

ALTER TABLE reporting.pipeline_runs
    ADD CONSTRAINT ck_reporting_pipeline_runs_status
    CHECK (status IN ('running', 'succeeded', 'failed', 'unknown', 'skipped'));

CREATE UNIQUE INDEX IF NOT EXISTS uq_reporting_pipeline_runs_prefect_run_id
    ON reporting.pipeline_runs (prefect_run_id)
    WHERE prefect_run_id IS NOT NULL;
