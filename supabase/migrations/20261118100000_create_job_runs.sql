CREATE TABLE IF NOT EXISTS public.job_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name TEXT NOT NULL,
    target_date DATE NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_job_runs_job_name_target_date
    ON public.job_runs (job_name, target_date);

ALTER TABLE public.job_runs ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS ix_job_runs_processing
    ON public.job_runs (job_name, status)
    WHERE status = 'processing';
