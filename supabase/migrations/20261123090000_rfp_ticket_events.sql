-- Notificaciones en tiempo real (SSE): outbox de eventos de tickets RFP.
-- Modelo SQLModel: mock_data/pipelines/rfp_intake/models.py (TicketEvent)

BEGIN;

CREATE TABLE IF NOT EXISTS public.rfp_ticket_events (
    id         BIGSERIAL PRIMARY KEY,               -- id: de SSE y cursor de Last-Event-ID
    ticket_id  UUID NOT NULL REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    event_type VARCHAR(40) NOT NULL,
    payload    JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rfp_ticket_event UNIQUE (ticket_id, event_type)
);

ALTER TABLE public.rfp_ticket_events ENABLE ROW LEVEL SECURITY;

COMMIT;
