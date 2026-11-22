-- Generación y evaluación de respuestas a RFP (Hito 9, Parte 2): borradores por departamento, EvaluationResult
-- estructurado por iteración y handoff a la Parte 3. Modelos SQLModel: mock_data/pipelines/rfp_intake/models.py

BEGIN;

-- Ticket: etapa de la Parte 2 (la de la Parte 1 se conserva en pipeline_stage) y contrato rfp_response_handoff.v1.
ALTER TABLE public.rfp_tickets
    ADD COLUMN IF NOT EXISTS response_stage    VARCHAR(16)
        CHECK (response_stage IN ('generando', 'evaluando', 'handoff', 'fallido')),
    ADD COLUMN IF NOT EXISTS response_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS response_handoff  JSON;

-- Sección: último borrador + EvaluationResult (draft_content / evaluation_results ya existían) y su estado.
ALTER TABLE public.rfp_department_sections
    ADD COLUMN IF NOT EXISTS section_status     VARCHAR(24) NOT NULL DEFAULT 'pendiente'
        CHECK (section_status IN ('pendiente', 'generando', 'en_evaluacion', 'evaluacion_superada', 'needs_human_review')),
    ADD COLUMN IF NOT EXISTS iterations         INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS needs_human_review BOOLEAN NOT NULL DEFAULT FALSE;

-- Historial del ciclo generador-evaluador: un EvaluationResult estructurado por iteración.
CREATE TABLE IF NOT EXISTS public.rfp_section_evaluations (
    id                     SERIAL PRIMARY KEY,
    ticket_id              UUID NOT NULL REFERENCES public.rfp_tickets (ticket_id) ON DELETE CASCADE,
    department_id          VARCHAR(16) NOT NULL CHECK (department_id IN ('warehouse', 'lastmile', 'reverse')),
    run                    INTEGER NOT NULL DEFAULT 1,
    iteration              INTEGER NOT NULL CHECK (iteration >= 1),
    draft_content          TEXT NOT NULL,
    readability            JSON NOT NULL DEFAULT '{}',   -- { pass, score, details }
    relevance              JSON NOT NULL DEFAULT '{}',   -- { pass, missing_aspects[], unsupported_figures[], off_scope[] }
    compliance             JSON NOT NULL DEFAULT '{}',   -- { pass, rule_ids[], violations[], rules_checked[] }
    overall_pass           BOOLEAN NOT NULL,
    feedback_for_generator TEXT NOT NULL DEFAULT '',
    generator_meta         JSON NOT NULL DEFAULT '{}',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rfp_section_evaluation UNIQUE (ticket_id, department_id, run, iteration)
);
CREATE INDEX IF NOT EXISTS ix_rfp_section_evaluations_ticket_id ON public.rfp_section_evaluations (ticket_id);

ALTER TABLE public.rfp_section_evaluations ENABLE ROW LEVEL SECURITY;

COMMIT;
