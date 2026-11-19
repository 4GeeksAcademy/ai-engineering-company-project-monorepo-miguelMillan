CREATE TABLE IF NOT EXISTS public.task_dead_letters (
    task_id UUID PRIMARY KEY,
    task_name TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    error TEXT NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_task_dead_letters_failed_at
    ON public.task_dead_letters (failed_at DESC);
