-- Aprobación humana por departamento y cierre del documento (Hito 9, Parte 3).
-- Modelos SQLModel: mock_data/pipelines/rfp_intake/models.py
-- El checkpointer de LangGraph (langgraph-checkpoint-postgres) crea sus propias tablas con PostgresSaver.setup():
-- checkpoints, checkpoint_writes, checkpoint_blobs y checkpoint_migrations. Aquí solo se les activa RLS si existen.

BEGIN;

-- Ticket: hilo del checkpointer (rfp-{ticket_id}:approval-{run}), etapa del flujo de aprobación y arbitrajes (§7).
ALTER TABLE public.rfp_tickets
    ADD COLUMN IF NOT EXISTS approval_thread_id VARCHAR(120),
    ADD COLUMN IF NOT EXISTS approval_run       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS approval_stage     VARCHAR(16)
        CHECK (approval_stage IN ('iniciando', 'en_curso', 'esperando', 'completado', 'bloqueado', 'fallido')),
    ADD COLUMN IF NOT EXISTS approval_note      TEXT,
    ADD COLUMN IF NOT EXISTS arbitration        JSON NOT NULL DEFAULT '[]';

-- Sección: aprobación del responsable del BRIEFING §2.1.
ALTER TABLE public.rfp_department_sections
    ADD COLUMN IF NOT EXISTS approval_round    INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS approval_request  JSON,
    ADD COLUMN IF NOT EXISTS decision_history  JSON NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS commitments       JSON NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS approved_content  TEXT,
    ADD COLUMN IF NOT EXISTS decided_by        VARCHAR(255);

ALTER TABLE public.rfp_department_sections DROP CONSTRAINT IF EXISTS rfp_department_sections_approval_status_check;
ALTER TABLE public.rfp_department_sections ADD CONSTRAINT rfp_department_sections_approval_status_check
    CHECK (approval_status IN ('pendiente', 'esperando_aprobación', 'procesando', 'en_revision', 'aprobada', 'rechazada'));

-- FinalDocument (BRIEFING §2.3): ticket_id, sections, currency, generated_at.
CREATE TABLE IF NOT EXISTS public.rfp_final_documents (
    ticket_id    UUID PRIMARY KEY REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    rfp_id       VARCHAR(80) NOT NULL,
    currency     VARCHAR(3)  NOT NULL CHECK (currency IN ('USD', 'EUR')),
    sections     JSON NOT NULL DEFAULT '[]',
    approvals    JSON NOT NULL DEFAULT '[]',
    arbitration  JSON NOT NULL DEFAULT '[]',
    content      TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trazabilidad: una fila por ejecución de nodo (agente, input, output, timestamps) en las tres partes.
CREATE TABLE IF NOT EXISTS public.rfp_trace_events (
    id            SERIAL PRIMARY KEY,
    ticket_id     UUID NOT NULL REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    part          VARCHAR(16) NOT NULL CHECK (part IN ('intake', 'response', 'approval')),
    thread_id     VARCHAR(120),
    node          VARCHAR(64) NOT NULL,
    agent         VARCHAR(80) NOT NULL,
    department_id VARCHAR(16),
    status        VARCHAR(16) NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),
    input         JSON NOT NULL DEFAULT '{}',
    output        JSON NOT NULL DEFAULT '{}',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rfp_trace_events_ticket_id ON public.rfp_trace_events (ticket_id, id);

ALTER TABLE public.rfp_final_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rfp_trace_events ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['checkpoints', 'checkpoint_writes', 'checkpoint_blobs', 'checkpoint_migrations'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        END IF;
    END LOOP;
END $$;

COMMIT;
