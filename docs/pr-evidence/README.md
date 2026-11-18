# Evidencias para el PR

- `cron-proposal.svg`: plantilla cron propuesta; no instalada en el host.
- `successful-run-example.svg` / `successful-run-example.log`: formato de log de éxito **ilustrativo**, no ejecución real.
- `nightly-export-failed.svg` / `nightly-export-failed.log`: fallo real capturado. El job se detuvo antes de escribir CSV o ejecutar pipeline porque `public.job_runs` no existe en la BD conectada.
- `telemetry-csv-first-rows.svg` y `telemetry-2026-11-15-first-rows-redacted.csv`: primeras 3 filas de un snapshot real de 39 eventos producido mediante `scripts.nightly_export.export_telemetry`; IDs de evento/sesión/usuario/request redactados en la evidencia. Se eliminó el snapshot completo del workspace tras extraer la muestra para no dejar IDs sin redactar.

**Limitación:** no se pudo obtener una ejecución exitosa real ni probar el pipeline completo. `reporting.pipeline_runs` y `public.job_runs` no existen en la BD accesible; el log de éxito se etiqueta explícitamente como ilustrativo.
