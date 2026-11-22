-- Recepción de RFP (Hito 9, Parte 1): Ticket, metadatos RFP y DepartmentSection en PostgreSQL.
-- Modelos equivalentes en SQLModel: mock_data/pipelines/rfp_intake/models.py

BEGIN;

CREATE TABLE IF NOT EXISTS public.rfp_tickets (
    ticket_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfp_id            VARCHAR(80)  NOT NULL,
    status            VARCHAR(32)  NOT NULL DEFAULT 'analizando'
        CHECK (status IN ('analizando', 'descartado', 'analisis_completo', 'generando_borrador', 'en_evaluación',
                          'needs_human_review', 'esperando_aprobación', 'terminado')),
    pipeline_stage    VARCHAR(32)  NOT NULL DEFAULT 'recibido'
        CHECK (pipeline_stage IN ('recibido', 'convertido', 'metadatos', 'clasificado', 'orquestado', 'workers',
                                  'sintetizado', 'handoff', 'fallido')),
    raw_pdf_path      VARCHAR(500) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    uploaded_by       VARCHAR(255),
    attempts          INTEGER      NOT NULL DEFAULT 0,
    last_error        TEXT,
    discard_reason    TEXT,
    handoff           JSON,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rfp_tickets_status ON public.rfp_tickets (status);
CREATE INDEX IF NOT EXISTS ix_rfp_tickets_rfp_id ON public.rfp_tickets (rfp_id);

CREATE TABLE IF NOT EXISTS public.rfp_metadata (
    ticket_id          UUID PRIMARY KEY REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    client_name        VARCHAR(255),
    client_country     VARCHAR(2) CHECK (client_country IN ('US', 'ES')),
    currency           VARCHAR(3) CHECK (currency IN ('USD', 'EUR')),
    services_requested JSON NOT NULL DEFAULT '[]',
    monthly_volume     INTEGER,
    sku_count          INTEGER,
    deadline           VARCHAR(10),
    deadline_text      VARCHAR(120),
    budget_range       VARCHAR(120),
    departments_needed JSON NOT NULL DEFAULT '[]',
    readability        JSON NOT NULL DEFAULT '{}',
    details            JSON NOT NULL DEFAULT '{}',
    classification     JSON,
    markdown           TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.rfp_department_sections (
    id                 SERIAL PRIMARY KEY,
    ticket_id          UUID NOT NULL REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    department_id      VARCHAR(16) NOT NULL CHECK (department_id IN ('warehouse', 'lastmile', 'reverse')),
    key_aspects        JSON NOT NULL DEFAULT '[]',
    open_questions     JSON NOT NULL DEFAULT '[]',
    worker_meta        JSON NOT NULL DEFAULT '{}',
    draft_content      TEXT,
    evaluation_results JSON,
    approval_status    VARCHAR(24) NOT NULL DEFAULT 'pendiente',
    approver           VARCHAR(120),
    approved_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rfp_section_ticket_department UNIQUE (ticket_id, department_id)
);
CREATE INDEX IF NOT EXISTS ix_rfp_department_sections_ticket_id ON public.rfp_department_sections (ticket_id);

ALTER TABLE public.rfp_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rfp_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rfp_department_sections ENABLE ROW LEVEL SECURITY;

COMMIT;
