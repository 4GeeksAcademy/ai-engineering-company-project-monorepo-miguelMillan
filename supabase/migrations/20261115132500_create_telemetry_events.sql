CREATE TABLE IF NOT EXISTS public.telemetry_events (
    event_id UUID PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    session_id UUID,
    user_id VARCHAR(66),
    event_type VARCHAR(64) NOT NULL,
    schema_version VARCHAR(16) NOT NULL,
    request_id UUID,
    tags JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_telemetry_events_timestamp
    ON public.telemetry_events (timestamp);

CREATE INDEX IF NOT EXISTS ix_telemetry_events_event_type
    ON public.telemetry_events (event_type);

CREATE INDEX IF NOT EXISTS ix_telemetry_events_tags_gin
    ON public.telemetry_events USING GIN (tags);

ALTER TABLE public.telemetry_events ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.reject_telemetry_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'telemetry_events rows are immutable'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS telemetry_events_immutable ON public.telemetry_events;
CREATE TRIGGER telemetry_events_immutable
    BEFORE UPDATE OR DELETE ON public.telemetry_events
    FOR EACH ROW
    EXECUTE FUNCTION public.reject_telemetry_event_mutation();
