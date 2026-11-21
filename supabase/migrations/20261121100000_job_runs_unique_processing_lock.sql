-- El lock del job nocturno pasa a ser el propio estado `processing`, garantizado por la base de datos.
--
-- Antes: create_job hacía LOCK TABLE public.job_runs IN SHARE ROW EXCLUSIVE MODE y el índice
-- ix_job_runs_processing NO era único, así que dos filas `processing` del mismo job eran posibles
-- en cuanto alguien quitara el LOCK TABLE: la exclusión venía de bloquear la tabla, no del estado.
--
-- Ahora: un índice ÚNICO parcial permite como máximo una fila `processing` por job_name. La
-- transición pending → processing es un único UPDATE atómico; si otra ejecución ya está en
-- `processing`, ese UPDATE viola el índice y la ejecución se cancela. Sin LOCK TABLE.
--
-- Precondición: no puede haber dos filas `processing` del mismo job (la creación fallaría y la
-- migración se revierte entera). Comprobado antes de aplicar: 0 filas `processing`.

BEGIN;

DROP INDEX IF EXISTS public.ix_job_runs_processing;

CREATE UNIQUE INDEX IF NOT EXISTS ux_job_runs_one_processing_per_job
    ON public.job_runs (job_name)
    WHERE status = 'processing';

COMMIT;
