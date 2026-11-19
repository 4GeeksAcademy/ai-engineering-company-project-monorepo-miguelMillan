# Progress Log — TrackFlow

## Evaluación técnica del modelo de pronóstico de ventas, 2026-11-19

- Contexto nuevo (evaluación del modelo) archivado en `memory-bank/history/SALES-FORECAST-MODEL-EVALUATION-CONTEXT-2026-11-19.md`. No había copia previa; verificado con `cmp`. `CONTEXT.md` sin modificar. Trabajo en la rama actual `feature/regression-model-eval`.
- Reproducibilidad confirmada: `train_sales_forecast.py` vuelve a dar RMSE 3,67 %, K2 0,92 y Gini 0,93 en prueba, con diferencias solo de redondeo (decimal 15, por `n_jobs`). Se restauró `metrics.json` para no meter ruido en el diff. Sin dependencias nuevas.
- Nuevo `scripts/evaluate_sales_forecast.py`:
  - `TimeSeriesSplit` de 5 folds, ventana expansiva, cada fold valida un año completo de 2019 a 2023; 2024–2025 sin tocar.
  - Chequeo explícito de orden cronológico.
  - Sin fuga por fold: imputación y features solo con los meses anteriores al corte, y validación recursiva sin valores reales.
  - MAE y RMSE de entrenamiento y validación como media ± desviación estándar.
  - Curva de aprendizaje en `mock_data/eval/learning_curve.png` con el suelo de ruido.
  - Experimento de acción correctiva.
- Resultados:
  - Validación: RMSE 50.898 ± 12.818 EUR (4,89 ± 1,60 %) y MAE 43.149 ± 11.697 EUR.
  - Entrenamiento: RMSE 2,65 %, frente a un suelo de ruido de 2,57 %.
  - Diagnóstico: underfitting localizado en el nivel anual, sin overfitting. El sesgo alterna de signo con el crecimiento X±Y.
  - Acción correctiva: anclar el total anual al crecimiento de Y−2 → RMSE 39.339 ± 5.007 EUR en CV, mejora en 4 de 5 folds. Añadirlo como feature se probó y no bastó.
- Reporte `mock_data/eval/evaluation_report.md`: RMSE como métrica de negocio (MSE pedido por el CONTEXT y picos de noviembre–diciembre); recomienda no promover a staging sin la corrección.
- Tests: `tests/pipelines/test_sales_forecast_evaluation.py` (4 tests: orden cronológico, rechazo de folds barajados o invertidos, sin fuga en el borde del fold, MAE/RMSE). `uv run pytest tests/pipelines/test_sales_forecast*.py -q` → 10 passed.
- Pendiente: implementar el ancla como opción del entrenamiento, validarla con horizonte de 24 meses y solo después evaluar una vez en 2024–2025. Sin commit.

## Archivo de contexto del modelo de pronóstico de ventas, 2026-11-19

- Archivada copia literal de `CONTEXT.md` en `memory-bank/history/SALES-FORECAST-MODEL-CONTEXT-2026-11-19.md`. Antes se comparó con `cmp -s` contra todos los archivos de `memory-bank/history/`: ninguno era idéntico y no existía copia de este contexto. La copia se verificó con `cmp` (idéntica).
- `CONTEXT.md` sin modificar. Pauta del desarrollador: siempre que el contexto sea nuevo, crear su respaldo en `history/` al trabajar sobre él (sin duplicar si ya existe uno idéntico).

## Modelo de pronóstico de ventas (Random Forest), 2026-11-19

- Reescrito `scripts/train_sales_forecast.py` según el CONTEXT.md de pronóstico de ventas, con datos de `mock_data/raw/trackflow_sales.csv` (equivale a `data/raw/`). Target: `revenue_eur` de `market == "consolidated"`.
- Nulos: vacíos, no numéricos y valores ≤0 pasan a NaN; los meses faltantes 2016-01..2025-12 se reinsertan; la interpolación temporal se hace por partición después del split. El CSV actual no tiene nulos (0 imputados).
- Split por año: 2016–2023 train (96 meses) y 2024–2025 test (24 meses). El pronóstico recursivo a 24 meses no consume valores reales de prueba.
- Escalado: target = log del crecimiento interanual, features como ratios logarítmicos de la serie y `StandardScaler` ajustado solo con train. Se corrige así que RF no extrapole la tendencia (la versión previa en EUR tenía RMSE 10,7 %).
- Se eligió Random Forest por el tamaño de datos, la explicabilidad y el tiempo de ajuste; está documentado en el docstring y en `scripts/README.sales_forecast.md`.
- Métricas de test: MSE 2,28e9 EUR², RMSE 3,7 % del ingreso medio, K2 (interpretado como R²) 0,92, Gini normalizado 0,93, PSI del crecimiento interanual 0,09 (estable) y PSI del ingreso por envío 0,35 (posible cambio de mix US/ES, a revisar). La línea base "repetir 2023" tiene RMSE 9,5 %.
- Visualización `reports/sales_forecast/sales_forecast_test_2024_2025.png` con banda P10–P90 de 500 trayectorias por árbol. También se generan `metrics.json` y `predictions_2024_2025.csv`.
- Pruebas `tests/pipelines/test_sales_forecast.py` (6): split 8/2, rechazo de datasets que no tienen 10 años, sin fuga (alterar ×50 los años de prueba no cambia train ni pronóstico), causalidad de features, imputación por partición y PSI.
- `pyproject.toml`: añadido pytest como dev y `pythonpath = ["."]`.
- Validación: `uv run python scripts/train_sales_forecast.py` correcto; `uv run pytest tests/pipelines/test_sales_forecast.py -q` → 6 passed. `uv run pytest tests` falla al recolectar las pruebas preexistentes de nightly/pipeline porque el entorno uv raíz no tiene `sqlalchemy`; con `python -m pytest` del sistema → 21 passed, sin cambios.
- Pendiente: decidir si `reports/sales_forecast/` se versiona como evidencia del PR; no se hizo commit.

## Entorno raíz para modelo de ventas, 2026-11-19

- Verificado `uv 0.13.9`; al no existir `pyproject.toml` en la raíz se inicializó el proyecto con `uv init --bare`.
- Agregadas con `uv add` las dependencias `scikit-learn`, `xgboost`, `pandas` y `matplotlib`; uv creó `.venv` y `uv.lock`.
- Validación: import de los cuatro paquetes con `uv run` correcto; `git diff --check` limpio. No se instaló con pip ni pipenv.
- Se conservaron cambios preexistentes del usuario en `CONTEXT.md` y `mock_data/raw/trackflow_sales.csv`. Este cambio solo prepara el entorno, no implementa aún el modelo.

## Endurecimiento de tareas Celery asíncronas, 2026-11-19

- Cambiado el mensaje Celery para que transporte solo el UUID `task_id`: la API guarda el CSV en `backend_data` y el worker lo carga mediante referencia desde el volumen compartido. El input se elimina tras éxito/fallo terminal y se limpian archivos CSV huérfanos de más de 7 días al arrancar una tarea.
- Extendida la configuración de Celery con entrega persistente y visibilidad Redis; todas las excepciones procesables tienen hasta 3 reintentos con backoff exponencial y jitter (4 intentos incluyendo el original). La DLQ registra en `public.task_dead_letters` el número de reintentos/ejecuciones, traceback y datos de tarea.
- Normalizado estado `revoked` a `failure`; añadidas pruebas del contrato liviano, almacenamiento por referencia, ciclo de estados, retry config y escritura simulada de DLQ.
- Validación: `uv run --directory services/api pytest -q tests/test_tasks.py` → 10 passed; suite API completa → 80 passed, 1 warning Starlette/httpx. Python compileall, `node --check services/api/src/server.js` y `git diff --check` pasaron.
- Compose no pudo validarse/levantarse: el archivo `.env` requerido no está presente; una validación con variables placeholder confirmó que también es requerido el archivo `.env` por `env_file`. No se verificó la tarea con infraestructura real, el objetivo de latencia <200 ms ni Flower con tareas demostrativas. Se aclara en README que el procesamiento no bloquea el POST pero recepción/persistencia del upload escala con tamaño y no permite garantizar <200 ms absoluto.
- Se conservaron cambios preexistentes del usuario, incluyendo `CONTEXT.md`, README, gateway y la entrada previa del progreso. Pendiente operativo: configurar `.env`, aplicar migración DLQ, levantar Redis/API/worker/Flower y smoke test E2E.

## Análisis de incidencias asíncrono con Celery, 2026-11-18

- Desacoplado `POST /api/incidents/analyze`: el gateway Node conserva su contrato público y delega enqueue a FastAPI; Celery corre `analyze_csv_text()` en un worker aparte, con Redis broker/backend, resultados JSON de 24 h y eventos observables en Flower. Redis en Compose usa AOF, volumen persistente y `noeviction`.
- Conservadas validaciones de CSV (extensión, UTF-8 y límite 10 MiB), respuesta 202 con `task_id`, polling de estado y exportación CSV vinculada al ID de tarea. La interfaz backoffice consulta estado hasta terminar.
- Añadidos reintentos con backoff exponencial/jitter solo para excepciones operativas transitorias (`OSError`, `TimeoutError`), y persistencia de fallos terminales con traceback en `public.task_dead_letters`; añadida migración SQL, aún no aplicada a BD.
- El adaptador privado gateway→FastAPI requiere `TASKS_INTERNAL_TOKEN`, comparado en tiempo constante; el token debe definirse igual en gateway y worker/API mediante `.env`. Flower requiere `FLOWER_BASIC_AUTH`.
- Verificación: `uv run --directory services/api pytest -q` → 76 passed, 1 warning de deprecación Starlette/httpx; `node --check services/api/src/server.js` y `git diff --check` pasaron. Redis aislado respondió `PONG`, mostró `noeviction` y un worker real conectó, registró `tasks.analyze_incident_csv` y llegó a `ready`; el contenedor de verificación fue eliminado. Build del backoffice pasó antes de los últimos cambios (que no afectan el frontend).
- No verificados: stack Compose completo/Flower por ausencia del `.env` requerido, ejecución E2E de una tarea con worker vivo ni escritura DLQ en PostgreSQL; no se aplicó migración. Hubo una invocación de pruebas desde raíz que no recogió el entorno correcto; repetida desde el directorio configurado de API con resultado correcto.
- `CONTEXT.md` queda sin modificar. Siguientes pasos operativos: configurar secretos en `.env`, levantar Compose, aplicar migración en entorno DB autorizado y ejecutar smoke test end-to-end.

## Archivo de contexto del job nocturno, 2026-11-18

- Archivada una copia literal de `CONTEXT.md` en `memory-bank/history/NIGHTLY-EXPORT-CRONJOBS-CONTEXT-2026-11-18.md`, a petición explícita del usuario. Se buscó primero y no existía una copia histórica del contexto de este hito.
- `CONTEXT.md` permanece sin modificar. No se creó una segunda copia ni se alteró otra entrada histórica.

## Evidencias para PR del job nocturno, 2026-11-18

- Se generó `docs/pr-evidence/cron-proposal.svg` con la expresión `15 2 * * *`; corresponde a una plantilla de crontab, no a un cron instalado. Se mantiene cron del sistema porque el job es batch standalone, desacoplado de FastAPI.
- La BD accesible contiene `public.telemetry_events` pero no `public.job_runs` ni el esquema reporting; por eso la corrida completa no puede tener éxito. Se capturó un log real de fallo en `docs/pr-evidence/nightly-export-failed.log` al faltar `public.job_runs`.
- No se alteraron ni aplicaron migraciones ni se ejecutó el pipeline sobre esa BD. La ejecución exitosa permanece explícitamente como ejemplo ilustrativo en `docs/pr-evidence/successful-run-example.log` (no evidencia real).
- Se llamó el exportador real para `2026-11-15`, generando 39 filas; se conservó solo la muestra de primeras 3 filas con IDs de evento/sesión/usuario/request redactados (`docs/pr-evidence/telemetry-2026-11-15-first-rows-redacted.csv` y SVG). Se borró del workspace el snapshot completo tras obtener la muestra.
- `git diff --check` limpio. Pendiente instalar cron y aplicar migraciones en entorno apropiado para obtener logs de éxito y bloqueo real.

## Auditoría de aceptación del job nocturno, 2026-11-18

- Revisados los criterios de independencia, estados, exclusión, idempotencia, fallos, CSV, separación de tablas, logs, cron y `TARGET_DATE`.
- Confirmado por código que `scripts/nightly_export.py` corre standalone y lanza el pipeline semanal como subproceso; el CSV queda en `mock_data/raw/` porque este checkout no contiene `data/`, según aclaración previa del usuario. `public.job_runs` se mantiene aparte de `reporting.pipeline_runs`.
- El lock actual combina una fila durable `processing` con `LOCK TABLE ... SHARE ROW EXCLUSIVE` para serializar el chequeo/creación; las pruebas unitarias verifican SQL/flujo simulado, pero no demuestran concurrencia real en PostgreSQL. La comprobación de entorno encontró `DATABASE_URL` definida, sin imprimirla; queda pendiente verificar conexión/migración y ejecutar dos procesos concurrentes.
- Corregido `TARGET_DATE` para aceptar únicamente `YYYY-MM-DD` y añadidas pruebas para fechas con padding/formato ISO extendido inválidos. El runner intenta marcar `failed` ante cualquier `BaseException` propagada, incluidos KeyboardInterrupt/SystemExit, pero una terminación abrupta del proceso/host no puede ejecutar recuperación: el registro puede quedar `processing` indefinidamente. Documentada la intervención operativa requerida; no hay lease ni recuperación automática.
- El ejemplo cron se documenta como plantilla no instalada. La configuración efectiva depende del crontab/entorno del host. Logging del runner tiene timestamp/job/status; salida del subproceso se hereda a stdout/stderr y el cron de ejemplo la redirige.
- Validación posterior: `python -m pytest tests/test_nightly_export.py tests/test_job_runner.py -q` → 10 passed; `py_compile`, `git diff --check` y diagnósticos del editor limpios. Sin conexión/migración confirmada no afirmar lock concurrente demostrado ni operación completa sobre Supabase.

## Revisión de idempotencia y observabilidad del job nocturno, 2026-11-18

- Sustituido `services/nightly_job.py` por el módulo requerido `services/job_runner.py`, con funciones de crear/actualizar/consultar ejecuciones (`create_job`, `update_job`, `has_processing_lock`, `has_completed_for_date`). El control usa filas de `public.job_runs`, sin una tabla ni columna de lock aparte.
- El chequeo de processing y completed más la inserción se serializan con `LOCK TABLE ... SHARE ROW EXCLUSIVE`; una segunda instancia registra cancelación `failed` con mensaje y no exporta. Si ya hay `completed` para `(nightly_export, target_date)`, registra una invocación no-op `completed` y no relanza CSV ni pipeline.
- Toda excepción del runner se registra a nivel ERROR con traceback y se propaga; se intenta persistir `failed` y `finished_at`. Logs incluyen timestamp y `job=nightly_export`, además del estado resultante. `TARGET_DATE` sigue siendo opcional.
- Actualizada migración con índice parcial de `processing`; actualizado README con el detalle del mecanismo.
- Pruebas: `python -m pytest tests/test_nightly_export.py tests/test_job_runner.py -q` → 7 passed tras añadir el caso de cancelación; `py_compile` pasó. `git diff --check` detectó whitespace final, corregido después; no se probó concurrencia real ni PostgreSQL.

## Job nocturno de exportación de telemetría, 2026-11-18

- Añadida la migración `supabase/migrations/20261118100000_create_job_runs.sql` para `public.job_runs`, con `target_date` obligatorio, estados controlados e índice compuesto `(job_name, target_date)`. Se mantiene separada de `reporting.pipeline_runs`.
- Implementado `scripts/nightly_export.py`: resuelve `TARGET_DATE` o ayer UTC, exporta una sola vez el CSV `mock_data/raw/telemetry_YYYY-MM-DD.csv` y lanza el pipeline ya existente `mock_data/pipelines/pipeline.py` como subproceso. La fuente del pipeline continúa siendo PostgreSQL, no el snapshot.
- Implementado `services/nightly_job.py` con estados, advisory lock, registro de cada invocación y recuperación como `failed` de filas `pending`/`processing` dejadas por una terminación previa. Una ejecución duplicada omite el trabajo; una repetición de un día completado queda registrada como no-op completado.
- Elegido cron del sistema operativo para desacoplar la tarea de FastAPI. Documentados motivos y expresión `15 2 * * *` en `scripts/README.es.md`; añadido `scripts/nightly_export.cron.example`.
- Actualizado `AGENTS.md`: nuevas entradas de `memory-bank/progress.md` siempre al principio, y como máximo una copia histórica por contexto/hito. No se creó copia de `CONTEXT.md`.
- Corregida la ruta de los datos al directorio real `mock_data/` (este checkout no tiene `data/`).
- Validación: `python -m pytest tests/test_nightly_export.py -q` → 3 passed; `python -m py_compile scripts/nightly_export.py services/nightly_job.py` y `git diff --check` correctos. No se ejecutó contra PostgreSQL ni se probó un pipeline real; la migración debe aplicarse y cron configurarse en el host.
- El cambio previo del usuario en `CONTEXT.md` permanece sin modificar por esta tarea; no se archivó ni duplicó.

## Data Pipelines — Mejoras de resiliencia (actividad adicional), 2026-11-16

### Implementación de mejoras documentadas en el diseño

Revisadas las "Preguntas de resiliencia y observabilidad" del `PIPELINE_DESIGN.md` para identificar mejoras de resiliencia/observabilidad que fueron especificadas en el diseño pero aún no implementadas. Se identificaron 5 mejoras, de las cuales se implementaron 3:

#### ✅ Implementadas:

1. **Advisory lock para corridas concurrentes** (ya estaba en código)
   - Función `_weekly_pipeline_run_lock(week_start)` en `mock_data/pipelines/pipeline.py` (líneas 69-93)
   - Usa `pg_try_advisory_lock` con namespace `7412`
   - Previene múltiples flows procesando simultáneamente la misma semana
   - Registra `skipped`/`already_running` si no se adquiere el lock

2. **Checkpoints y fases para recuperación** (ya estaba en código)
   - Tabla `reporting.pipeline_runs` incluye `phase` (extracting/transforming/loading/completed) y `last_checkpoint_at`
   - Se actualizan explícitamente en cada etapa (líneas 527-595)
   - Permite recuperación post-fallo desde último checkpoint conocido sin asumir estados de commit incompletos

3. **Idempotencia en ingesta de telemetría con `ON CONFLICT DO NOTHING`** (implementado ahora)
   - Cambio en `services/api/routes/telemetry.py` (líneas 101-146)
   - Usa `postgresql_insert(...).on_conflict_do_nothing(index_elements=['event_id'])`
   - Distingue `stored` (nuevos) de `already_present` (duplicados)
   - Un reintento con el mismo lote ya no genera error de clave primaria ni duplica
   - Logging mejorado registra `already_present` para observabilidad

#### ⏸ No implementadas (alcance más amplio):

4. **Buffer persistente en IndexedDB (frontend)**
   - Requeriría cambios en `uis/backoffice` con riesgos de eventos obsoletos y pérdida en limpieza de caché
   - Aplazado para iteración futura

5. **Encabezado `Idempotency-Key` para lotes**
   - La ingesta ya es idempotente por `event_id`; requerería tabla de caché de lotes
   - Aplazado para iteración futura

**Documentación actualizada:**
- `mock_data/pipelines/PIPELINE_DESIGN.md`: añadida sección "Mejoras de resiliencia e idempotencia implementadas" (líneas finales) con justificación de cada mejora y por qué se priorizó.
- `services/api/README.md`: actualizada descripción de `POST /telemetry/events` para documentar el comportamiento idempotente y la distinción entre `stored`/`already_present`.

**Validación:**
- `uv run --directory services/api pytest tests/test_inventory_telemetry.py -q` → 4 passed (pruebas telemetría sin cambios)
- Cambios de código verificados: imports de sqlalchemy dialects PostgreSQL correctos, lógica de conteo de duplicados robusta.

**Notas:**
- Las tres mejoras implementadas responden directamente a preguntas de resiliencia documentadas en el diseño.
- Ambas mejoras no implementadas son candidatas para futuras iteraciones y no rompen la implementación actual.
- Todas las mejoras implementadas fortalecen la observabilidad, recuperabilidad y eliminan puntos de fallo de duplicación silenciosa.

## Data Pipelines — Fase 3 (CLI verificado) y Fase 4 (Dashboard de negocio), 2026-11-16

### Fase 3: CLI entrypoint verificado tras refactor
- Confirmado que `python mock_data/pipelines/pipeline.py --help` funciona correctamente después de la refactorización en subflows.
- Instalado `prefect>=3,<4` globalmente como fallback para permitir ejecución del CLI sin requerir virtual env específico.
- El entrypoint mantiene su API: `--week-start YYYY-MM-DD` (opcional, por defecto última semana cerrada) y `--trigger {scheduled,manual,late_event}`.
- No se requería base de datos real para validar la sintaxis y estructura del CLI; solo confirmación de que los imports resuelven y el argparse funciona.

### Fase 4: Dashboard de desempeño obligatorio para stakeholders no técnicos
- Creado componente `uis/backoffice/src/app/reporting/ReportingPages.tsx` que consume `GET /reporting/weekly-warehouse-client-performance`.
- El dashboard muestra los 4 KPIs de HITO para cada combinación warehouse/client_id:
  1. **Volumen de entrada** (inbound_units_count): suma de unidades recibidas, con ícono TrendingUp azul.
  2. **Throughput de salida** (outbound_orders_count): cantidad de pedidos despachados, con ícono TrendingDown verde.
  3. **Frecuencia de quiebre de stock** (stockout_events_count): veces que el inventario cayó bajo mínimo, con ícono AlertTriangle ámbar.
  4. **Tasa de discrepancia** (discrepancy_rate): porcentaje de precisión inventario, con ícono Zap rojo.
- Cada KPI está claramente etiquetado con su nombre exacto del HITO, mostrando el período de semana (lunes a domingo UTC) en formato legible para ejecutivos.
- Interfaz responsive (mobile-first, grid 1-2-4 columnas), sin traducciones, sin gráficos complejos — enfoque en legibilidad de datos para CEO (Thomas) y Head of Warehouse Operations (Ana).
- Integración en App.tsx:
  - Importado `ReportingPage` desde `./app/reporting/ReportingPages`.
  - Agregado `'reporting'` a las vistas posibles (`activeView` state).
  - Añadido botón "Desempeño" en navegación principal del backoffice, vinculado a `/reporting`.
  - Ruta condicional en el router principal: `path === '/reporting'` renderiza el componente en contexto de la aplicación.
- Estados de carga y error manejados con componentes existentes (`AsyncErrorState`) y fallback con spinner.
- Telemetría integrada: tracking de `backoffice_section_viewed` y `frontend_data_load_failed`.
- Validación: `npm run build` en backoffice compiló sin errores TypeScript ni Vite. Generadas pruebas de tipo correcto.
- El endpoint `/reporting/weekly-warehouse-client-performance` ya estaba implementado en `services/reporting/routes.py` y es responsable de consumir los datos de `reporting.weekly_warehouse_client_performance`.

### Estado de completud
- ✅ Fase 1: Refactorización en subflows (completado 2026-11-16)
- ✅ Fase 2: Tests unitarios (completado 2026-11-16)
- ✅ Fase 3: CLI entrypoint operativo tras refactor (completado 2026-11-16)
- ✅ Fase 4: Dashboard de negocio obligatorio (completado 2026-11-16)
- ⏸ Próximos pasos: Testing e2e en URL pública con PageSpeed >= 80 (opcional post-commit)

## Data Pipelines — subflows y tests unitarios, 2026-11-16
- Refactorizado el pipeline existente en cuatro subflows Prefect: extracción, transformación, carga y exportación opcional del snapshot. El coordinador los invoca secuencialmente con inputs/outputs explícitos; la exportación usa `return_state=True` y su fallo no invalida la carga confirmada.
- Conservados el entrypoint CLI, locks, checkpoints, contadores, reintentos SQL y staging/upsert sobre `reporting.weekly_warehouse_client_performance`. Se mantiene `mock_data/`, nombre actual de la antigua carpeta `data/`. Solo se extraen los cuatro tipos de `telemetry_events` exigidos por el hito; no se añadió una fuente de dominio innecesaria.
- Movida la transformación a `mock_data/process/weekly_performance.py`: preparación/validación de eventos y tres tasks KPI independientes para unidades de entrada, conteos de eventos y razón de discrepancias. Se conservan dimensiones almacén/cliente/semana ISO UTC, deduplicación, rechazo explícito de datos inválidos y cero cuando no hay salidas.
- Creado `tests/pipelines/test_pipeline.py`: pruebas en memoria mediante `.fn`, sin servidor Prefect, bases de datos ni APIs. Las tres tasks KPI tienen inputs propios de prueba; se comprueban cantidades calculadas a mano, separación de dimensiones, razón 1/2, denominador cero, cantidades malformadas, cliente ausente, deduplicación, offsets UTC y entrada vacía.
- Prefect no estaba disponible ni declarado: añadido `prefect>=3,<4` mediante `uv add --directory services/api --no-sync`; regenerados `services/api/uv.lock` y `services/requirements.txt` mediante uv. Instalación de validación aislada con Python 3.12 y Prefect 3.9.3; sin cambios al Python compartido.
- Validaciones: `python -m pytest tests/pipelines/test_pipeline.py` -> 11 passed, incluida la repetición focalizada tras aislar los fixtures. `python mock_data/pipelines/pipeline.py --help` correcto. Smoke adicional con servidor Prefect local temporal e IO simulado: subflows reales, KPIs exactos, orden de checkpoints, fallo opcional contenido y transformación inválida que impide cargar. Los errores de ese smoke fueron inyectados deliberadamente y sus estados se comprobaron.
- Actualizados los README del pipeline y el diseño con contratos y comandos reproducibles. `git diff --check` correcto. Archivado el contexto activo íntegro en `memory-bank/history/HITO-DATA-PIPELINES-SUBFLOWS-CONTEXT-2026-11-16.md`; `cmp` confirmó coincidencia byte a byte, incluido el final sin salto de línea. Conservado el histórico de KPIs del 2026-11-15.
- Alcance: fases 1 y 2 solicitadas. No se modificaron frontend, layouts, telemetría técnica, secretos ni infraestructura. El cambio local previo de `CONTEXT.md` pertenece al desarrollador y se conserva. Sin commit ni push.
- Pendiente: corrida real del script contra PostgreSQL con conexiones y migraciones de reporting configuradas; no se ejecutó ETL ni se escribió en Supabase. El dashboard del contexto general queda para otra fase, fuera de esta solicitud.

## Data Pipelines — documentación final verificada, 2026-11-15
- Confirmado por el desarrollador que `data/pipelines/PIPELINE_DESIGN.md` es el archivo final y está corregido.
- Queda constancia de cumplimiento completo: sección `Current State`, brecha de negocio, extracción desde `telemetry_events`, payload y cadencia, propósito con KPIs de TrackFlow, diagrama extracción → transformación → carga, estrategia de actualizaciones e idempotencia, log `pipeline_runs` con nombres/tipos/justificación, mapeo a Prefect y sus tasks/states/blocks.
- También quedan documentados los tres endpoints de `services/reporting/` y las funciones de `data/pipelines/` que invocan, separados de `services/telemetry/` y `GET /telemetry/report`.
- El diseño mantiene los nombres de dominio `warehouse`, `client_id`, `week_start`, `inbound_units_count`, `outbound_orders_count`, `stockout_events_count`, `discrepancy_events_count` y `discrepancy_rate`, y el destino `reporting.weekly_warehouse_client_performance`.
- Las cinco fases de diseño están cerradas y el contexto final ya fue archivado en `memory-bank/history/HITO-DATA-PIPELINES-CONTEXT-2026-11-15.md`.

## Data Pipelines — preguntas de resiliencia y observabilidad, 2026-11-15
- Añadidas respuestas específicas para duplicados de `eventId`, reintentos post-fallo, eventos tardíos y reconstrucción por `week_start`.
- Documentadas señales para distinguir silencio real de fallo de captura: heartbeat, estados de `pipeline_runs`, contadores de ingesta y sesiones activas.
- Definida la trazabilidad `eventId` → `requestId` → `telemetry_events` → `run_id` → snapshot reporting.
- Documentada la recuperación por checkpoint de fase, el buffer offline futuro en IndexedDB, reintentos con `Idempotency-Key` y respuestas `stored`/`already_present`.
- Definido el control de corridas concurrentes mediante lock por `week_start` y `run_id` único. No se modificó `CONTEXT.md` ni se archivó otra copia.

## Data Pipelines — integración con la aplicación, fase 5, 2026-11-15
- Diseñados los tres endpoints separados de `services/reporting/`: estado de la última corrida, disparo manual y feed de KPIs semanal.
- Cada endpoint delega explícitamente en `data/pipelines/runs`, `data/pipelines/flows` o `data/pipelines/queries`; ninguna lógica ETL se ubica en `services/`.
- Los contratos usan el vocabulario de TrackFlow: `warehouse`, `client_id`, `week_start`, `inbound_units_count`, `outbound_orders_count`, `stockout_events_count`, `discrepancy_events_count` y `discrepancy_rate`.
- Completadas las cinco fases de diseño del pipeline; `memory-bank/history/HITO-DATA-PIPELINES-CONTEXT-2026-11-15.md` es la única copia final archivada y `cmp` confirmó que coincide con `CONTEXT.md`.

## Data Pipelines — mapeo a Prefect, fase 4, 2026-11-15
- Mapeado el diseño a `weekly_warehouse_client_performance_flow` con tasks de extracción, transformación, carga y registro de corrida.
- Documentados los estados `Running`, `Completed` y `Failed`, junto con reintentos para errores transitorios y parada ante errores de contrato, datos o permisos.
- Definidos los Prefect blocks para conexiones Supabase de lectura/escritura, configuración runtime, secretos y notificaciones.
- Aclarado que Prefect orquesta y observa, mientras el lock por semana y la transacción atómica preservan la idempotencia en la base de datos.
- No se implementó código Prefect; no se archivó `CONTEXT.md` porque queda una fase pendiente.

## Data Pipelines — resiliencia e idempotencia, fase 3, 2026-11-15
- Documentada en `PIPELINE_DESIGN.md` la carga transaccional con staging temporal, lock por `week_start`, eliminación de filas obsoletas y `INSERT ... ON CONFLICT` sobre `(warehouse, client_id, week_start)`.
- Definido el comportamiento ante rollback, timeout posterior al commit y reintento: reconstrucción completa del snapshot sin duplicar ni sumar resultados previos.
- Diseñado el log `reporting.pipeline_runs` con identidad de corrida, pipeline, semana, timestamps UTC, estado controlado, contadores de extracción/transformación/carga, error sanitizado y origen del disparo.
- No se implementaron tablas ni código; no se archivó `CONTEXT.md` porque las cinco fases aún no han terminado.

## Data Pipelines — diseño del pipeline de desempeño, fase 2, 2026-11-15
- Ampliado `PIPELINE_DESIGN.md` con el propósito de negocio, KPIs y eventos fuente obligatorios, contrato de extracción desde `public.telemetry_events` y frecuencia semanal.
- Documentado el flujo extracción → transformación → carga con Mermaid, incluyendo normalización UTC, extracción de `tags`, agrupación por `warehouse` + `client_id` + `week_start` ISO y cálculo de `discrepancy_rate`.
- Definida la estrategia para relecturas y correcciones: deduplicación por `event_id`, reconstrucción completa de la semana y upsert por la restricción única `(warehouse, client_id, week_start)`, con eliminación de filas obsoletas de la semana.
- Fijados el destino exacto `reporting.weekly_warehouse_client_performance` y los endpoints separados de telemetría para consulta, estado de corrida y disparo manual.
- No se implementaron tablas, endpoints ni `services/reporting/`; no se archivó `CONTEXT.md` porque las cinco fases aún no han terminado.

## Data Pipelines — análisis del estado actual, fase 1, 2026-11-15
- Creado `PIPELINE_DESIGN.md` con la sección `Estado Actual`: captura y almacenamiento actuales de `telemetry_events`, alcance del reporte técnico de ingeniería y brecha de negocio.
- Documentada la separación entre `services/telemetry/`/`GET /telemetry/report` y el futuro pipeline de negocio bajo `services/reporting/`.
- Identificada la pregunta pendiente: desempeño semanal por almacén y cliente, con volumen de entrada, throughput de salida, frecuencia de quiebre de stock y tasa de discrepancia.
- Registrados como requisitos para fases posteriores: frecuencia semanal, grano `warehouse` + `client_id` + `week_start` ISO UTC y tabla `reporting.weekly_warehouse_client_performance`.
- No se modificaron telemetría, tablas de destino ni endpoints de reporting; no se archivó `CONTEXT.md` porque las cinco fases aún no han terminado.
## Telemetría — feedback del PR #25: carga SQL y caché, 2026-11-16
- Las cinco funciones de `services/telemetry/analysis.py` reciben ahora una sesión SQL y cargan sus eventos mediante un helper común. Los límites temporales se aplican solo en SQL y los timestamps se convierten a UTC antes de agrupar. Autenticación y latencia filtran `event_type` en SQL; volumen, dominancia y errores conservan todos los tipos necesarios para sus conteos y denominadores. Solo latencia selecciona `tags`.
- Eliminada la carga de DataFrames de la ruta. El caché usa las fechas de entrada, incluidos valores omitidos, en lugar de fechas resueltas con microsegundos; mantiene el periodo original durante 60 segundos y renueva la ventana al expirar. Se conserva el contrato de tres métricas dentro de `period`/`metrics`.
- Documentadas las funciones, consultas, fórmulas, límites del caché y ejemplo JSON en `services/api/README.md`. Actualizada y publicada la descripción del PR #25 con métricas, JSON y validaciones; el PR ya estaba fusionado y no tenía hilos de revisión. La descripción advierte que las correcciones de código siguen locales, sin commit ni push.
- Validación: el primer intento con `uv` no pudo arrancar porque no estaba instalado. Instalado exclusivamente en el directorio temporal de esta sesión, con entorno Python 3.12 aislado; `uv run --directory services/api --python 3.12 pytest -q tests/test_inventory_telemetry.py` pasó (4 passed; advertencia conocida de Starlette/httpx).
- Smoke ejecutado con SQLite en memoria y TestClient: cinco métricas correctas, filtros y proyecciones SQL inspeccionados, límites inclusivos, offsets UTC equivalentes, ventanas vacías, JSON exacto, aciertos de caché con fechas explícitas/omitidas/parciales sin nuevas consultas, expiración exacta a los 60 segundos y errores HTTP 422. No se añadieron archivos de tests ni se escribió en Supabase.
- `git diff --check` correcto. Reglas de UI/layout: no aplican, frontend intacto. Sin cambios de rutas protegidas salvo este progreso autorizado.
- Contexto utilizado: `cmp CONTEXT.md memory-bank/history/HITO-TELEMETRY-CONTEXT-2026-11-15.md` confirmó coincidencia íntegra. Se conserva ese archivo histórico sin sobrescribirlo ni duplicarlo.
- Siguiente paso: commit/push y una nueva entrega de las correcciones, porque el PR #25 ya está fusionado. La validación remota sobre Supabase no se ejecutó en esta tarea.

## Telemetría — ajuste del contrato JSON del reporte, 2026-11-15
- Ajustado `GET /telemetry/report` para exponer exactamente `period` y `metrics` con las claves `events_per_day`, `error_rate_by_type` y `auth_failure_rate`.
- Las funciones `latency_by_route` y `dominant_event_types` permanecen disponibles en el pipeline, pero no forman parte del contrato JSON solicitado.
- Validado con SQLite temporal que no existen claves adicionales en la respuesta; `git diff --check` correcto.
- No se crea otro archivo histórico porque `CONTEXT.md` no cambió desde el archivo verificado en `HITO-TELEMETRY-CONTEXT-2026-11-15.md`.

## Telemetría — revisión de criterios y archivo de contexto, 2026-11-15
- Revisión completada: `services/telemetry/analysis.py` existe y contiene cinco métricas independientes, puras y serializables; todas convierten timestamps con `pd.to_datetime(..., utc=True)` antes de agrupaciones temporales.
- La carga SQL ocurre una vez en el endpoint y entrega `DataFrame` a las funciones puras; el refinamiento, conversión numérica necesaria, agrupación y agregación se realizan con Pandas, sin loops de cálculo.
- `GET /telemetry/report` acepta fechas ISO opcionales, resuelve los últimos siete días UTC por defecto, devuelve `period`/`metrics` y usa caché en memoria con TTL de 60 segundos por periodo.
- Las métricas tienen dimensiones temporales, por tipo, ruta o día y responden a volumen, errores, latencia, dominancia de eventos y fallos de autenticación; no incluyen KPIs de negocio.
- Archivado el contexto íntegro utilizado en `memory-bank/history/HITO-TELEMETRY-CONTEXT-2026-11-15.md`; `cmp` confirmó que coincide byte a byte con `CONTEXT.md`.

## Telemetría — métrica de autenticación y dashboard técnico, 2026-11-15
- Añadida `auth_failure_rate_by_day` en `services/telemetry/analysis.py`, calculando `login_failed / (login_failed + login_succeeded)` por día en Pandas.
- El endpoint carga los eventos de autenticación mediante `event_type IN ('login_failed', 'login_succeeded')` y expone la métrica bajo `metrics.auth_failure_rate`. Se usaron los nombres reales instrumentados en el catálogo, que no llevan prefijo `user_`.
- Creada la vista `/telemetry` en `uis/backoffice/src/app/telemetry/TelemetryDashboard.tsx`, con tablas HTML accesibles para las métricas operacionales y mostrando explícitamente `period.from` y `period.to`.
- Añadido enlace de navegación desde `uis/backoffice/src/App.tsx`; la vista consume el endpoint autenticado existente y no agrega KPIs de negocio.
- Validación: `uv run --directory services/api pytest -q tests/test_inventory_telemetry.py` (4 passed), smoke test aislado de la métrica de autenticación correcto, `python -m compileall` correcto y `npm --prefix uis/backoffice run build` correcto. Solo permanece la advertencia conocida deprecada de Starlette/httpx.
- Esta fase no archiva `CONTEXT.md`; el cierre histórico queda pendiente hasta completar todas las fases.

## Telemetría — endpoint de reporte y caché, fase 2, 2026-11-15
- Implementado `GET /telemetry/report` en `services/api/routes/telemetry.py` con `start_date` y `end_date` opcionales en ISO 8601; sin parámetros resuelve una sola vez `now - 7 días` y `now`, ambos UTC.
- El endpoint consulta `telemetry_events` una vez por periodo, crea el `DataFrame` y pasa exactamente el periodo resuelto a las métricas. La respuesta incluye `period` y las métricas `events_per_day`, `error_rate_by_type`, `latency_by_route` y `dominant_event_types`.
- Añadido caché en memoria por combinación de fechas con TTL de 60 segundos; una petición repetida dentro del TTL devuelve el resultado sin recalcular ni consultar de nuevo.
- Corregido el uso de `sqlmodel.select` para obtener instancias `TelemetryEventRecord` y añadido compatibilidad de importación cuando FastAPI se ejecuta desde `services/api`.
- Validación: `uv run --directory services/api pytest -q tests/test_inventory_telemetry.py` (4 passed) y smoke test aislado del endpoint con SQLite temporal (ingestión, periodo explícito, periodo por defecto y cache hit) correctos. Solo permanece la advertencia conocida deprecada de Starlette/httpx.
- Esta fase no archiva `CONTEXT.md`; la fase 3 queda pendiente.

## Telemetría — análisis Pandas, fase 1, 2026-11-15
- Creado `services/telemetry/analysis.py` con cuatro funciones puras y serializables: volumen de eventos por ventana temporal/tipo, tasa de errores por ventana, latencia media por ruta API y tipos de evento dominantes.
- Todas convierten `timestamp` a `datetime` UTC antes de agrupar; la latencia extrae `route_group` y `duration_ms` desde `tags` y la tasa usa el catálogo técnico de eventos fallidos, sin métricas de negocio ni loops de cálculo.
- Añadidos `services/__init__.py` y `services/telemetry/__init__.py` para exponer el módulo como paquete Python. Añadida dependencia `pandas` y regenerado `services/requirements.txt` con `uv pip compile`.
- Validación: `python -m compileall -q services/telemetry`, `git diff --check -- services/telemetry services/api/pyproject.toml services/requirements.txt` y smoke test Pandas con `uv run --with pandas python ...` correctos. La primera ejecución del smoke test desde `services/api` falló únicamente por el directorio de importación; se repitió desde la raíz y pasó.
- Esta fase no archiva `CONTEXT.md`; el endpoint `/telemetry/report` y la caché quedan para las fases siguientes.

## Telemetría — proxy same-origin en Codespaces, 2026-11-15
- El preflight directo a `8081.app.github.dev` recibe 401 del túnel privado antes de llegar al middleware CORS. Para evitar CORS sin tocar el frontend, el endpoint quedó configurado en runtime como `/telemetry/events` y Vite reenvía esa ruta a FastAPI en `127.0.0.1:8081`.
- Verificación HTTP por el proxy same-origin: un evento inválido devolvió `{"received":1,"stored":0,"rejected":1}`; no se escribió en Supabase. Los puertos 5174 y 8081 responden y `git diff --quiet HEAD -- uis` confirma que no cambió el frontend.
- Esta configuración es del proceso de Codespaces; no se añadió configuración al código del frontend. La conectividad del navegador debe probarse con la página abierta desde el puerto 5174.

## Telemetría — dataset de inventario dev cargado, 2026-11-15
- Ampliado `services/api/inventory_seed.py` de forma idempotente y documentado en el README: 8 SKU, 15 entradas, 15 salidas, dos mínimos de stock y una auditoría discrepante. Conserva los datos previos y añade 2 SKU, 10 entradas y 12 salidas.
- Confirmado con el usuario que la instancia es dev/demo y que el alcance era solo inventario, sin escribir eventos en `telemetry_events`.
- Ejecutado `uv run --no-sync --directory services/api seed-inventory` en Supabase: 2 SKU, 10 entradas, 12 salidas, 1 auditoría y 2 mínimos configurados. Verificación remota de solo lectura: totales 8/15/15/1; tres categorías, cuatro clientes y ambos almacenes; saldos 58/60 y 74/75 en los dos casos de umbral; discrepancia 30 frente a 28. `telemetry_events` sigue en 0.
- Idempotencia probada dos veces sobre SQLite temporal: primera ejecución 8/15/15/1 y segunda 0 inserciones; también se comprobó que la base temporal de seed no escribe en telemetría.
- Validación: `uv run --no-sync --directory services/api pytest -q tests/test_inventory_telemetry.py` (4 passed; advertencia deprecada Starlette/httpx) y `git diff --check -- services/api/inventory_seed.py services/api/README.md` limpio.

## Telemetría — evidencia solicitada para PR, 2026-11-15
- Consulta de solo lectura: `telemetry_events` tiene 0 filas en Supabase; todavía no puede tomarse la captura requerida de cinco eventos reales.
- Reproducción del lote mixto en SQLite temporal, sin tocar Supabase: HTTP 200 con `{"received":3,"stored":2,"rejected":1}`; se almacenaron el evento de negocio `inbound_order_created` y el técnico `login_failed`, y se rechazó un payload con clave extra.
- `git diff --quiet HEAD -- uis` confirmó que el frontend no tiene cambios rastreados. El backoffice puede generar eventos reales mediante navegación (`backoffice_section_viewed`, `api_request_completed`) y operaciones confirmadas de entrada/salida; hay que ejecutarlas y esperar el flush antes de capturar Supabase.

## Supabase — columnas de SKU aplicadas, 2026-11-15
- Ejecutada la rutina existente `initialize_sql_database` contra el host Supabase configurado. Añadió `client_id` y `minimum_stock` a `public.sku` y completó `client_id` para los seis SKU existentes; quedaron cero SKU sin cliente.
- `minimum_stock` quedó sin configurar en los seis SKU, ya que el seed/contexto no define umbrales concretos. No se asignaron valores inventados ni se ejecutó el seeder.

## Revisión de semillas y esquema Supabase, 2026-11-15
- HITO05 sí había registrado la ejecución de `seed-inventory` en Supabase. Comparación remota de solo lectura contra `services/api/inventory_seed.py`: coinciden exactamente los 6 SKU, 5 entradas y 3 salidas esperados; no se omitió por falta del secreto.
- Hallazgo pendiente: en Supabase las tablas `sku`, `stock_entry` y `stock_exit` existen, pero `sku` aún carece de `client_id` y `minimum_stock`. El código actual los agrega/backfillea en `initialize_sql_database`; no se inició la API ni se alteró el esquema durante esta revisión.
- Distinciones: los 15 proveedores exactos de HITO09 se guardan en TinyDB, no en Supabase. La semilla de 8–10 SKU y 15–20 entradas/salidas del contexto de Telemetría es sugerida para una fase futura; `telemetry-plan.md` declara que no se crea en fase 1. El seed actual sigue siendo el de HITO05 (6/5/3). No se ejecutó ningún seeder.

## Telemetría — migración aplicada en Supabase, 2026-11-15
- Confirmada la migración local `supabase/migrations/20261115132500_create_telemetry_events.sql` y aplicada al proyecto Supabase configurado por `DATABASE_URL`.
- Verificación remota de solo lectura: `public.telemetry_events` existe con las ocho columnas esperadas, los tres índices (timestamp, event_type, GIN(tags)), RLS habilitado y trigger `telemetry_events_immutable` habilitado para UPDATE y DELETE. La tabla contiene 0 filas; no se insertaron eventos artificiales.
- Confirmado en la copia de trabajo que el endpoint real está en `services/api/routes/telemetry.py`: valida por evento con `TelemetryEvent.model_validate`, inserta el lote con una operación bulk y devuelve los tres contadores. El frontend no se modificó.
- No se ejecutó el endpoint contra Supabase ni desde el navegador; el smoke HTTP de fase anterior fue local y aislado. La verificación de eventos reales queda pendiente de generar actividad legítima desde el backoffice.

## Telemetría — revisión de Supabase con configuración disponible, 2026-11-15
- Consulta de solo lectura con `DATABASE_URL`: conecta a PostgreSQL, pero `public.telemetry_events` no existe en esa base (`to_regclass` devolvió `NULL`). No se aplicó la migración ni se insertaron datos remotos; siguen sin verificarse filas de producción.
- Revisión estática de la migración local: define ocho columnas, tres índices explícitos (timestamp, event_type y GIN(tags)), RLS y trigger que bloquea UPDATE/DELETE. El endpoint valida evento por evento con `TelemetryEvent.model_validate` más el JSON Schema, realiza un único `session.execute(insert(...), rows)` y conserva `properties` íntegro como `tags`.
- Smoke HTTP aislado con SQLite temporal: evento de negocio y evento técnico válidos más una propiedad fuera del allowlist; HTTP 200 devolvió `received=3, stored=2, rejected=1`; se consultaron las dos filas y se confirmó la conservación de timestamp y tags. La suite `uv run --no-sync --directory services/api pytest -q tests/test_inventory_telemetry.py` pasó (4 tests; advertencia deprecada de Starlette/httpx).
- Comparación contra HEAD: no hay cambios bajo `uis/` y el modelo `TelemetryEvent` no tiene cambios; el frontend sigue leyendo `NEXT_PUBLIC_TELEMETRY_ENDPOINT`. No se verificó el valor efectivo del entorno ni se ejecutó el flujo real desde navegador.
- Bloqueo/siguiente paso: aplicar la migración en el proyecto Supabase correcto y repetir inspección de columnas/índices/trigger; después, comprobar eventos reales técnicos y de negocio. No se generaron filas artificiales en Supabase porque la tabla debe ser inmutable.

## Telemetría — validación de allowlists, 2026-11-15
- Añadida validación complementaria con JSON Schema Draft-07 y `FormatChecker` por evento, después de `TelemetryEvent.model_validate(...)`. Se mantienen inalterados los campos/modelo `TelemetryEvent`; los payloads con claves extra o dimensiones inválidas se rechazan individualmente y los `properties` admitidos se guardan íntegros en `tags`.
- Declarada la dependencia `jsonschema` en `services/api/pyproject.toml`; regenerado `services/requirements.txt` universal. El esquema se carga desde el contrato compartido y se copia al contenedor mediante contexto Docker dedicado.
- Validaciones en entorno temporal aislado: `tests/test_inventory_telemetry.py` (4 passed) y smoke HTTP SQLite con evento de negocio válido, evento técnico válido y payload con clave extra (respuesta 3/2/1; dos filas con tags intactos).
- Supabase remoto/backoffice siguen sin verificar: `DATABASE_URL` no disponible y acceso a `.env`/`node_modules` denegado por política de exclusión.
- Contexto activo archivado íntegro en `memory-bank/history/TELEMETRY-ALLOWLIST-VALIDATION-CONTEXT-2026-11-15.md`; `cmp` confirma coincidencia byte a byte.

## Telemetría — revisión de checklist, 2026-11-15
- Revisión de código: modelo y migración declaran ocho columnas, índices timestamp/event_type/GIN(tags), y trigger que bloquea UPDATE/DELETE. La ruta usa una validación independiente por evento, un `session.execute(insert(...), rows)` por lote y respuesta `received`/`stored`/`rejected`. El bloque `TelemetryEvent` no aparece modificado en el diff; el frontend no tiene cambios locales.
- Limitación de contrato: `TelemetryEvent.properties` es `dict[str, object]`; `model_validate` no aplica los allowlists anidados del JSON Schema. El endpoint conserva `properties` íntegro como `tags`, por lo que las dimensiones aprobadas no se pierden, pero tampoco filtra propiedades extra.
- Evidencia limitada a entorno local SQLite: lote técnico mixto respondió 2/1/1 y se consultó su fila. No se verificó tabla remota, migración aplicada ni eventos de negocio originados por el backoffice.
- Para completar evidencia real: `DATABASE_URL` debe estar disponible en entorno autorizado y el backoffice/navegador debe poder ejecutarse. La política de exclusión bloqueó `.env` y `node_modules`; no se accedió por rutas alternativas.

## Telemetría — verificación end-to-end, fase 3 — 2026-11-15
- Verificación parcial aislada: levantada API local con SQLite temporal; `curl` envió un lote con `login_failed` válido y un `event_type` inválido; HTTP 200 devolvió `{"received":2,"stored":1,"rejected":1}`. Consulta SQLite confirmó `event_type`, timestamp y `tags`; base temporal eliminada y servidor detenido.
- No se generaron órdenes desde el backoffice ni se consultó la tabla de la instancia remota de Supabase. No afirmar verificación end-to-end real.
- Bloqueos: `DATABASE_URL` no está en el entorno; `psql`/CLI Supabase, servicios y Playwright no están disponibles. `docker compose ps` no pudo resolver variables requeridas. La política de exclusión denegó acceso a `.env`, `.env.example` y `node_modules`; no se intentó acceder por vías alternativas.
- Siguiente paso: habilitar una ejecución con acceso remoto autorizado a Supabase, configuración de runtime disponible y backoffice/browser accesibles; aplicar/verificar migración y repetir el flujo real.

## Telemetría — endpoint de persistencia, fase 2 — 2026-11-15
- Reemplazado el stub `POST /telemetry/events`: el envelope recibe una lista de objetos sin validar globalmente; cada elemento se valida de forma independiente con el `TelemetryEvent` existente y los inválidos no interrumpen el lote.
- Los eventos válidos se convierten a columnas de `telemetry_events` y `properties` se guarda íntegro en `tags`; una llamada bulk insert y una transacción persistente por lote. Respuesta `received`/`stored`/`rejected`; el frontend permanece intacto.
- Validaciones: smoke test HTTP con un evento válido y uno inválido (200, contadores 2/1/1 y dimensiones persistidas); `uv run --directory services/api pytest -q tests/test_inventory_telemetry.py` (4 passed).
- Bloqueo: la migración Supabase de fase 1 todavía no está aplicada/verificada en la instancia remota; este smoke test usó SQLite temporal.

## Telemetría — almacenamiento, fase 1 — 2026-11-15
- Añadido `TelemetryEventRecord` al metadata SQLModel para crear `telemetry_events` con campos del envelope y `tags` JSONB. El contrato de `properties` se conserva íntegro como `tags`, incluidas sus dimensiones de inventario.
- Creada migración Supabase con índices B-tree para `timestamp` y `event_type`, GIN para `tags`, RLS habilitado sin políticas de acceso directo y trigger que rechaza UPDATE/DELETE. No hay lógica de actualización o eliminación de eventos.
- Validación: `uv run --directory services/api pytest -q tests/test_inventory_telemetry.py` (4 passed); metadata de tabla/índices verificado en SQLite; DDL PostgreSQL compilado y comprobado con JSONB, timestamptz y GIN. Confirmado que `NEXT_PUBLIC_TELEMETRY_ENDPOINT` conserva su configuración en el servicio frontend; no se modificó el frontend.
- Bloqueo: no se aplicó ni verificó la migración contra una instancia remota de Supabase en esta sesión. Próximo paso: aplicar la migración en Supabase antes de implementar la persistencia del endpoint en la fase siguiente.

## Telemetría — instrumentación de métricas obligatorias — 2026-11-13
- Añadidos `client_id` y `minimum_stock` por SKU, migración idempotente para registros existentes y endpoint de configuración del mínimo.
- Las salidas confirmadas detectan el cruce real por debajo del umbral y devuelven una señal del SKU verificado; el backoffice la encola con `track()`.
- `PUT /inventory/products/{id}/stock` rechaza la edición directa con 409 y entrega dimensiones fiables para que el cliente registre `direct_stock_edit_rejected`; se omite si el SKU no existe.
- Creado `POST /inventory/audits` y el registro persistente del conteo físico frente al stock calculado; solo diferencias distintas de cero generan `inventory_discrepancy_detected`. Añadida UI para configurar mínimos y registrar conteos.
- Actualizados el plan de telemetría y el README de la API. Añadidas pruebas de cruce único, rechazo, discrepancia/conteo conciliado y migración de SKU legado.
- Validaciones: `cd services/api && uv run pytest tests/test_inventory_telemetry.py` (4 passed; advertencia deprecada de Starlette/httpx); `npm --prefix uis/backoffice run build` correcto; lint focalizado de los cuatro archivos frontend correcto.
- Contexto usado archivado y comparado en `memory-bank/history/TELEMETRY-CAPTURE-FEEDBACK-CONTEXT-2026-11-13.md`; el contexto activo difería del archivo del 2026-11-12, que se conserva intacto.
- Bloqueo: la política de exclusión impidió leer `services/api/.env.example`; no se inspeccionó ni modificó. Próximo paso: validar el contrato de identidad de cliente con Operaciones y, en fase de almacenamiento, resolver el outbox transaccional; sigue pendiente confirmar que la salida acredita el picking completo.

## Telemetría — preparación de captura — 2026-11-12
- Añadido el stub `POST /telemetry/events` en FastAPI con validación del lote, envelope estricto, versión `1.0.0` y los 14 `event_type` del contrato; registra cantidad y tipos, responde `{\"received\": N}` y no persiste datos.
- Registrado el router de telemetría en `services/api/main.py` y creada la variable local `NEXT_PUBLIC_TELEMETRY_ENDPOINT` en `uis/backoffice/.env.local` con destino `http://localhost:8000/telemetry/events`.
- Cruzado `docs/telemetry/event-schemas.json` con `TELEMETRY-PHASE1-CONTEXT-2026-11-11.md`: se conservaron `warehouse`, `client_id`, `product_id`, `product_category`, `quantity` y los nombres de dominio de TrackFlow.
- Validación focalizada: lote completo de dos eventos HTTP 200 con `received: 2`, logs con cantidad y ambos `event_type`, y payload incompleto HTTP 422; `TELEMETRY_ENDPOINT` se lee desde el entorno. `CONTEXT.md` ya tenía cambios del usuario y no se revirtió; el histórico usado queda archivado en `memory-bank/history/TELEMETRY-CAPTURE-PREP-CONTEXT-2026-11-12.md`.
- Estado: preparado únicamente el stub y la configuración; no se creó `TelemetryService` ni se instrumentó el backoffice.

## Telemetría — Fase 2, servicio frontend — 2026-11-12
- Creado `uis/backoffice/src/services/telemetry.ts` con cola en memoria, envío por lotes de 20 eventos o tras 10 segundos, `sendBeacon` al ocultar la pestaña y hasta tres reintentos con backoff exponencial antes de descartar.
- `track(eventType, properties)` es la única función pública de captura y genera automáticamente IDs, timestamp, sesión, usuario seudonimizado, versión de schema y request ID.
- `session.ts` crea y elimina el `sessionId` al iniciar/cerrar sesión; `main.tsx` inicializa el listener del servicio y Vite expone `NEXT_PUBLIC_TELEMETRY_ENDPOINT` mediante `envPrefix`.
- Validaciones: `npm --prefix uis/backoffice run build` correcto y lint correcto, con una advertencia preexistente en `AccountProfile.tsx` (`react(set-state-in-effect)`).
- Estado: servicio listo; todavía no se instrumentaron componentes ni eventos concretos.

## Telemetría — Fase 3, instrumentación amplia — 2026-11-12
- Instrumentados `backoffice_section_viewed`, `api_request_completed`, `frontend_unhandled_error`, `frontend_retry_requested`, `frontend_web_vital_recorded`, `login_attempted`, `login_succeeded`, `login_failed` y `session_expired`, con propiedades allowlisted y razones de autenticación controladas.
- Los commits de entrada/salida de inventario emiten `inbound_order_created`, `outbound_order_created` o `inventory_stock_loss_recorded` únicamente cuando el SKU incluye un `client_id` fiable; usan el warehouse normalizado, SKU, categoría, cantidad e `id` real de la orden.
- Extendidos `event-schemas.json` y el stub backend con `frontend_web_vital_recorded`; Web Vitals observados: CLS, INP, LCP, FCP y TTFB, con página normalizada.
- Validaciones: build correcto, lint correcto con la advertencia preexistente de `AccountProfile.tsx`, diagnósticos sin errores y `git diff --check` correcto. La verificación Playwright/Network no pudo ejecutarse porque Chromium no está instalado; FastAPI en puerto 8000 tampoco arrancó por falta de `SECRET_KEY` local.
- Bloqueos de negocio conservados: no existe `client_id` estable en la respuesta actual, ni flujo de mínimo configurado, edición directa rechazada o auditoría física; no se inventaron valores ni se marcaron esos eventos como verificados.

## Telemetría — verificación de cumplimiento — 2026-11-12
- Eliminados los fallbacks hardcodeados de la URL frontend/backend; `NEXT_PUBLIC_TELEMETRY_ENDPOINT` se consume en Vite y `TELEMETRY_ENDPOINT` queda declarado en `services/api/.env.example`.
- UUIDs de evento y sesión mantienen formato UUID incluso cuando el runtime no expone `crypto.randomUUID`; el servicio no envía lotes si falta la configuración frontend.
- Verificaciones: build, lint, JSON Schema, importación backend y TestClient HTTP 200 con `received: 2` correctos. La ejecución Network con Chromium quedó bloqueada por la librería del sistema `libatk-1.0.so.0`; los procesos temporales fueron detenidos.
- Estado: los puntos técnicos verificables están corregidos; siguen sin poder cumplirse honestamente `stock_threshold_triggered`, `direct_stock_edit_rejected` e `inventory_discrepancy_detected` hasta implementar sus fuentes de negocio y un `client_id` estable.

## Telemetría — fase 3 — 2026-11-11
- `docs/telemetry/telemetry-plan.md` clasifica los 14 eventos con esquema: nueve en stream y cinco en batch, con frecuencia o latencia objetivo provisional y justificación basada en urgencia operativa.
- Documentados outbox propuesto, reintentos y deduplicación, ventanas de login, límites de entrega, muestreo estratificado de API y ponderación, throttle de alertas de errores sin descartar sucesos, y navegación/reintentos en lote. Las métricas obligatorias y eventos de seguridad no se muestrean.
- Añadidos riesgos de sesgo, pérdida, privacidad, consistencia y brechas de negocio; excluidos renders, pulsaciones, capturas, credenciales, datos de destinatario y otros payloads o trazas sensibles.
- Validación documental: contraste automático de los 14 `event_type` del JSON Schema frente a la tabla, sin duplicados ni omisiones; nueve stream y cinco batch, con secciones de límites y riesgos presentes. Contexto activo ya archivado íntegro en `memory-bank/history/TELEMETRY-PHASE1-CONTEXT-2026-11-11.md`; `cmp` confirma coincidencia.
- Estado: estrategia documentada, sin implementar captura, colas, alertas, muestreo ni transporte. Pendientes las dependencias de identidad, umbrales, auditoría, picking y definición operativa de entrega/retención.

## Telemetría — fase 2 — 2026-11-11
- `docs/telemetry/event-schemas.json` sustituye el índice provisional por JSON Schema Draft-07: envelope de ocho campos, `schemaVersion` 1.0.0, allowlists cerrados y esquemas para los cinco eventos obligatorios y nueve oportunidades de cinco categorías.
- `telemetry-plan.md` documenta tipos, obligatoriedad/nulos, semántica y privacidad del envelope y las propiedades de los 14 eventos, seudonimización HMAC, IDs de sesión opacos, filtros contra datos sensibles y dependencias de captura. Las otras oportunidades de fase 1 permanecen en catálogo sin esquema aprobado.
- Validaciones ejecutadas con `jsonschema` y `FormatChecker`: schema Draft-07 válido, ejemplos aceptados para los 14 eventos y rechazo de claves extra, timestamp inválido y envelope incompleto. Comprobada correspondencia con los cinco nombres obligatorios del contexto, categorías y allowlists documentados.
- El contexto activo sigue coincidiendo byte a byte con `memory-bank/history/TELEMETRY-PHASE1-CONTEXT-2026-11-11.md`; se conserva sin duplicar. No se implementó captura, almacenamiento ni lógica de negocio; siguen pendientes identidad estable de cliente, sesión opaca, umbrales, auditoría y conciliación de rechazos sin dimensiones verificables.

## Telemetría — fase 1 — 2026-11-11
- Creado `docs/telemetry/telemetry-plan.md` con los cinco eventos obligatorios de `CONTEXT.md` y 32 oportunidades justificadas de inventario, autenticación, navegación, rendimiento, errores, incidencias y proveedores. Cada fila identifica punto, hipótesis y decisión.
- Creado `docs/telemetry/event-schemas.json` como índice válido y preliminar de los 37 eventos, su clasificación, las propiedades mínimas exigidas y los almacenes; todavía no es JSON Schema ni un contrato de captura.
- Contrastados el flujo real de inventario y los antecedentes HITO05. Bloqueos documentados: ausencia de `client_id` estable, mínimos configurables por cliente, flujo de conteo físico, detección explícita de edición directa y confirmación de picking; señal visual `<= 5` no equivale al umbral obligatorio.
- Validación documental ejecutada con Python: JSON válido, cinco nombres obligatorios extraídos de la sección de métricas del contexto, sin duplicados, clasificación coherente con el plan y propiedades/almacenes requeridos presentes. Dos intentos iniciales del verificador incluyeron el encabezado y otras tablas; se acotó a la sección correcta y el control pasó.
- Contexto activo archivado íntegro en `memory-bank/history/TELEMETRY-PHASE1-CONTEXT-2026-11-11.md`; `cmp` confirma identidad byte a byte. Sin cambios en la aplicación, datos ni pruebas de runtime; siguiente paso: esperar requisitos de fase 2.

## Caching — 2026-11-11
- Añadidos middleware de tiempos HTTP y caché de listado/resumen de incidencias por usuario y filtros, TTL 5 s, límite 32 entradas e invalidación sincronizada en creación y cambio de estado. La caché es por proceso y no coordina escrituras externas.
- Backoffice React/Vite: carga diferida de análisis CSV, proveedores e inventario; memoización de motivos de filas inválidas dependiente del informe.
- `scripts/benchmark_caching.py` genera 3000 incidencias variadas en bases temporales. Medianas HTTP con 3000: listado 47,32 a 11,30 ms; resumen 39,53 a 8,31 ms. Comparación local, no tráfico de producción.
- `CACHING_REPORT.md` documenta las 30 operaciones verificadas contra OpenAPI, hipótesis de frecuencia, TTL, invalidación, mediciones y limitaciones. Contexto activo archivado íntegro en `memory-bank/history/CACHING-CONTEXT-2026-11-11.md`; `cmp` confirma identidad byte a byte.
- Validaciones: cuatro pruebas de incidencias correctas; comprobación temporal de autenticación, aislamiento por usuario/filtros, TTL e invalidación POST/PATCH; build correcto con tres chunks separados; lint sin errores y advertencia preexistente en `AccountProfile`; diagnósticos sin errores.
- Pendientes: verificación visual y React Profiler/Network, tráfico y latencias de producción, PageSpeed en URL pública no disponible y evaluación de memoria/coherencia multiproceso. No se modificó la base real ni se creó commit.

## Serialization audit — verificación Fase 3 — 2026-11-10
- Reejecutada la suite backend completa tras los cambios de schema: 66 tests correctos; una advertencia conocida de deprecación Starlette/httpx.
- FastAPI temporal abrió `/docs` y `/openapi.json` con HTTP 200. No hay navegador local ni herramienta para accionar Swagger UI; se abrió la página con el launcher del host, pero `Try it out` manual no pudo ejecutarse. No se declara cumplido ese subcriterio.
- Como alternativa, se enviaron solicitudes HTTP a la instancia aislada y se compararon las claves exactas con los componentes OpenAPI: `GET /health`, `POST /users`, `POST /auth/login`, `GET /auth/me`, alta/listado/detalle de `/api/incidents`. Todas coincidieron, incluyendo la proyección sin `description` en listado y con `description` en detalle.
- `docs/serialization-audit.md` marca ✅ las 30 operaciones y documenta el método y la limitación del navegador. La instancia temporal se detuvo; `git diff --check` limpio. Contexto sin cambios; se conserva el histórico archivado en Fase 1.
- Estado: verificación automatizada y de payloads completada; falta interacción manual en Swagger UI por limitación del entorno. Sin commit; preservar cambios ajenos.

## Serialization audit — implementación Fase 2 — 2026-11-10
- Añadidos `HealthResponse`, `MessageResponse`, `UserRegistrationResponse`, `UserListItemResponse`, `ProfileResponse` y schemas específicos de incidencias; inventario ahora usa un acuse de creación con solo `id` y un `StockOrderListItemResponse` plano con `product_name`.
- Todas las rutas de FastAPI con cuerpo documentan `response_model`; los DELETE declaran `response_model=None` y responden 204. Ninguna ruta devuelve modelos SQLModel/ORM en crudo.
- Auth: respuestas de registro, forgot/reset/change no incluyen email ni credenciales; login mantiene token/expiración y `/auth/me` conserva email del llamante, con perfil anidado proyectado sin IDs. `NewPasswordRequest` ya tenía `extra="forbid"`; no requirió cambio.
- Seguridad/proyecciones: GET `/users` solo para admin y sin email; todas las rutas de inventario requieren sesión. La creación de incidencia fija `open`; el listado omite descripción y el backoffice obtiene detalle al expandir. El historial de inventario sustituye SKU anidado por nombre, conserva `sku_id` y `user_uuid` porque ambos se muestran.
- Documentación: `docs/serialization-audit.md` conserva la clasificación original como línea base y agrega el estado, schemas y decisión de relación de cada proyección. `CONTEXT.md` sigue siendo el mismo de Fase 1; cotejar el histórico existente y conservarlo si coincide.
- Validaciones: `uv run --directory services/api pytest -q`: 66 correctas, advertencia conocida de deprecación Starlette/httpx; build de backoffice correcto; OpenAPI confirma 30 operaciones con schema de éxito o 204 sin cuerpo; smoke SQLite temporal valida registro sin email, acceso protegido y movimiento aplanado; `get_errors` sin errores; `git diff --check` limpio; `cmp` confirma que el contexto coincide con el histórico existente.
- Estado: implementación de Fase 2 completada; sin cambios en reglas de negocio fuera de la forma de entrada/salida y control de acceso indicado. Sin commit; preservar modificaciones previas fuera de alcance.

## Serialization audit — 2026-11-10
- Fase 1 completada en `docs/serialization-audit.md`: inventariadas las 30 operaciones de FastAPI (29 de negocio y `GET /health`), con método, ruta, propósito, respuesta actual, clasificación y recomendación concreta por endpoint. Las rutas del gateway Node y la documentación automática quedan identificadas fuera del alcance.
- Clasificación contrastada con el OpenAPI generado: 14 ya serializadas, 11 parcialmente serializadas y 5 sin schema de respuesta explícito. Se confirmó que los DELETE con 204 no requieren cuerpo ni serializer.
- Revisión de auth: no hay respuestas con `password`/`hashed_password`; login devuelve token y expiración, recuperación usa confirmación genérica y registro repite el email aunque el cliente ignora el cuerpo. Se documentaron proyecciones de perfil y usuarios, la lista de usuarios accesible a cualquier sesión y el exceso de datos en historial de inventario e incidencias.
- Revisión de escrituras: anotados los campos extra aceptados por `/auth/reset-password` y `/auth/change-password` y que `IncidentCreate` permite definir un estado inicial distinto de `open`.
- Validación: comprobación ejecutable contra `app.openapi()` confirmó cobertura exacta de las 30 combinaciones método/ruta y recuentos 14/11/5; `cmp -s CONTEXT.md memory-bank/history/SERIALIZATION-AUDIT-CONTEXT-2026-11-10.md` correcto. No se modificó lógica de la API ni se ejecutaron pruebas de aplicación.
- Estado: fase de auditoría completada; siguiente fase puede añadir schemas faltantes y acordar proyecciones antes de cambiar contratos. Sin bloqueos. El progreso preexistente modificado en esta sesión se conservó y se amplió.

## Lighthouse — corrección de evidencia documental — 2026-11-09
- Corregidos `AUDIT.md` y `REPORT.md` para reflejar las cuatro capturas reales de `audit/before/` y las cuatro de `audit/after/`, separando móvil/escritorio y website/back office. Se añadieron los puntajes Lighthouse visibles y enlaces a los PNG existentes; se retiraron referencias a capturas e informes HTML/JSON que no están en esas carpetas.
- Se distinguieron las mediciones de las capturas `*.app.github.dev` de las medianas de tres corridas CLI en previews locales. No se presentan como entornos idénticos ni se atribuyen automáticamente sus diferencias a cambios concretos.
- Se aclaró que el SEO menor del back office (63 en las capturas posteriores y en la medición local final) es intencional por `Disallow: /` para evitar indexación de una herramienta interna; robots no sustituye autenticación ni control de acceso.
- Validación: `git diff --check` correcto tras editar cada documento; búsqueda de referencias antiguas a capturas/reportes inexistentes sin resultados; capturas inspeccionadas. Sin cambios de código ni pruebas de frontend/backend. El contexto activo ya coincide byte a byte con `memory-bank/history/LIGHTHOUSE-CONTEXT-2026-11-08.md`, por lo que se conserva ese histórico sin duplicarlo.
- Estado: documentación actualizada. Sin commit; no quedan bloqueos técnicos.

## Lighthouse — auditoría y optimizaciones — 2026-11-08
- Revisadas las skills instaladas `core-web-vitals`, `performance` y `web-perf`. Lighthouse CLI 13.6.0 y Chrome for Testing 156; línea base y resultados finales medidos tres veces en builds locales de producción, perfil móvil y URLs fijas `127.0.0.1:4173/` (website) y `127.0.0.1:4174/login` (backoffice). CrUX/INP no disponibles para estas pruebas locales de navegación.
- Baseline mediana/rango: website Performance 99 (83–100), Accessibility 100, Best Practices 100, SEO 92; TTFB 12 ms, LCP 1,544 ms, CLS 0,000929. Backoffice Performance 99 (97–99), Accessibility 100, Best Practices 100, SEO 82; TTFB 8 ms, LCP 1,550 ms, CLS 0.
- Causas raíz: sin `robots.txt`, Vite servía `index.html` con `text/html` para esa ruta; meta description ausente del HTML de backoffice; luego `Disallow: /` válido, intencional para la herramienta privada, dispara `is-crawlable` en Lighthouse.
- Commits separados: `156754d` añade robots válido para ambos (website `Allow: /`, backoffice `Disallow: /`); `2a70b79` añade meta description de backoffice; `4b983ed` extrae `AsyncErrorState` reutilizable en inventario, proveedores y resumen de incidencias.
- Final mediana/rango: website Performance 99 (97–99), Accessibility/Best Practices 100, SEO 100; TTFB 10 ms, LCP 1,563 ms, CLS 0,000932. Backoffice Performance 100 (100–100), Accessibility/Best Practices 100, SEO 63 por la política `Disallow: /`; TTFB 1 ms, LCP 1,538 ms, CLS 0. No se atribuye al refactor la variación de Performance; los KPI principales permanecen buenos y estables. Lint backoffice termina con advertencia preexistente `react(set-state-in-effect)` en `AccountProfile.tsx`.
- `AUDIT.md` documenta baseline, causas raíz y tres oportunidades de refactorización; `REPORT.md` compara puntuaciones, métricas e impacto. Ocho capturas PNG (cuatro originales, dos baseline local, dos posteriores) están en `audit/before/` y `audit/after/`. Las capturas previas del túnel `*.app.github.dev` se conservan y no se mezclan con previews locales porque Lighthouse CLI redirige el túnel a login.
- Validaciones: build website y backoffice correctos; lint backoffice con la advertencia ya indicada; `robots.txt` responde 200 `text/plain` y contenido correcto; Lighthouse reports generados con URLs idénticas antes/después. No se midieron datos de campo ni se hizo afirmación de mejora CrUX.
- Los informes HTML/JSON y trazas completos permanecen locales (61 MB); el commit final lleva documentación y capturas PNG, no las trazas. `CONTEXT.md` fue leído y se conservará una copia exacta en el histórico de Lighthouse.
- Contexto activo archivado íntegro en `memory-bank/history/LIGHTHOUSE-CONTEXT-2026-11-08.md`; `cmp` confirma identidad byte a byte con `CONTEXT.md`. El original se conservó intacto.

## INFRA-40 — verificación VPS y evidencia — 2026-11-08
- Revisión local: `docker compose config --quiet` correcto con el `.env` local presente. `docker compose ps -a` muestra `backend` `unhealthy` e `interfaces` `Created`; el healthcheck no conecta con FastAPI en `8081` porque la aplicación aborta al no resolver el host PostgreSQL de Supabase dentro del contenedor (`EAI_AGAIN`). Desde el host Codespaces, ese DNS sí resuelve.
- Configuración estática: mounts de código para `uis/website`, `uis/backoffice` y `services/api`; `uis/start.sh` lanza ambos Vite en 3000/3001. Los destinos efectivos observados solo por host/puerto son `backend:8080` y `backend:8081`; gateway usa `SUPPLIERS_HOST=backend`. No se verificó hot reload en navegador porque interfaces no está ejecutándose.
- Secretos: búsqueda acotada sin coincidencias en `docker-compose.yml` y ambos Dockerfiles. `.env` está ignorado y no está en el índice ni en el historial consultado. Hay `.dockerignore` en `uis/` y `services/`. No existe `.env.example`; un clon limpio requiere preparar `.env`, por lo que `docker compose up` sin configuración no está demostrado.
- Producción: el usuario confirmó en el VPS `backend` y `interfaces` `Up (healthy)` en `docker compose ps -a`; imagenes reconstruidas, backend healthy tras añadir su endpoint `/health`, e interfaces healthy tras completar la optimización inicial de Vite. El output pegado sirve como evidencia textual; falta adjuntar una captura de pantalla a la descripción del PR.
- Local Codespaces: `docker compose ps -a` sigue mostrando backend `unhealthy` e interfaces `Created`; el contenedor no resuelve el host DNS PostgreSQL (`EAI_AGAIN`), aunque el host sí resuelve. Esta limitación local no invalida el estado saludable confirmado en el VPS, pero el criterio de stack local completo sigue pendiente.
- Corregido el healthcheck de FastAPI añadiendo `GET /health` en `services/api/main.py`; `TestClient` con SQLite temporal devuelve HTTP 200 y payload de estado. `get_errors` sin errores; el commit `6da2de9` está en `origin/docker` y el VPS lo ejecuta.
- La copia `memory-bank/history/INFRA-40-VPS-EVIDENCE-CONTEXT-2026-11-08.md` conserva el mismo texto que `CONTEXT.md`, pero `cmp` detecta únicamente que el contexto raíz no tiene salto de línea final y el histórico sí; no se declara idéntica byte a byte.

## INFRA-40 — Docker de desarrollo — 2026-11-05
- Confirmado con el usuario mantener React/Vite, no migrar a Next.js. `uis/Dockerfile` usa Node 22 Alpine, instala cada app con `npm ci` y ejecuta `start.sh`: website 3000, backoffice 3001, hot reload y cierre coordinado. `.dockerignore` excluye dependencias, .next, dist, entornos, logs e informes.
- `services/Dockerfile` usa Python 3.12 oficial, instala uv y dependencias con `uv pip install --system -r requirements.txt`; requisitos exportados de `services/api/uv.lock`. Conserva Node para el gateway/analizador CSV y arranca Uvicorn con `--reload` mediante el supervisor existente. Exclusiones de cache Python, entornos, datos, tests y logs.
- `docker-compose.yml` tiene exactamente `interfaces` y `backend`, red bridge con nombre explicito tomado del entorno, bind mounts y volumen persistente TinyDB. No hay contrasenas, claves de respaldo ni URLs de conexion incrustadas en YAML/Dockerfiles. Los proxies y gateway usan `backend` como host; inventario prioriza la variable Docker sobre el entorno local de Vite.
- `.env` raiz creado antes de Compose, ignorado por Git y con permisos 0600; reutiliza la configuracion real de API sin imprimir secretos. Un comando inicial uso una ruta relativa bajo `uv --directory` y modifico por error `services/api/.env`; se recupero la copia previa del historial local de VS Code y `cmp` confirmo restauracion byte a byte. El entorno raiz posterior se preparo con rutas absolutas y validacion dotenv.
- Validaciones: `docker compose config --quiet`, construccion de ambas imagenes, sintaxis Node y diagnosticos del editor correctos; contenedor interfaces saludable y HTTP 200 en localhost:3000 y localhost:3001. Git confirma exclusion de ambos .env. No se crearon pruebas ni se ejecuto la suite completa. npm informo vulnerabilidades existentes (website: 2 altas; backoffice: 22 totales, 2 altas), sin actualizaciones fuera de alcance.
- Bloqueo de integracion: `docker compose up -d --wait --wait-timeout 120` falla porque el backend no resuelve el host PostgreSQL Supabase (`Temporary failure in name resolution`); la aplicacion no completa startup. La primera alternativa con PostgreSQL local tambien mostro timeout TCP entre contenedores y fue retirada para respetar los dos servicios. Interfaces arrancadas por separado con `--no-deps`; no declarar toda la plataforma operativa.
- Bloqueo VPS: no hay destino SSH documentado, configuracion .ssh ni agente de autenticacion disponible. El usuario indico no conocer usuario/host. No se realizo conexion SSH ni despliegue. Produccion requiere identificar VPS, provisionar acceso y entorno privado, resolver conectividad y preparar TLS/servidores sin recarga antes de sign-off.
- Revision final: los tres proxies efectivos usan `backend` como host; peticion desde interfaces al gateway tambien expira, confirmando un bloqueo de trafico entre contenedores ademas del DNS externo. README raiz actualizado con comandos, puertos, configuracion, persistencia y limites. Contexto activo conservado y archivado en `memory-bank/history/INFRA-40-CONTEXT.md`; `cmp` confirma identidad byte a byte. Compose y `git diff --check` correctos. Sin commit ni cambios en reglas o infraestructura protegida.

## HITO05 — preparación de persistencia PostgreSQL — 2026-11-05
- Confirmado que `services/api` ya contiene FastAPI y autenticación TinyDB. Añadidas las dependencias `sqlmodel` y `psycopg2-binary` mediante `uv add`; imports verificados correctamente.
- Tras el bloqueo de edición de Copilot, el usuario añadió `DATABASE_URL` manualmente en `services/api/.env`; se verificó solo la presencia de la clave, sin leer el valor.
- `services/api/database.py` conserva TinyDB y crea un motor SQLModel por aplicación en el lifespan, llama a `SQLModel.metadata.create_all(engine)` y expone `get_db` con una sesión por petición. No hay sesión global.
- Añadidos y registrados los endpoints `/inventory`: GET públicos para productos/detalle/órdenes; POST protegidos para SKU y movimientos. El stock se calcula por SKU y almacén; las salidas insuficientes se rechazan con 400 antes de insertar.
- Añadidos los modelos ORM `SKU`, `StockEntry` y `StockExit` y schemas independientes. `current_stock` solo aparece en `SKUResponse`; `user_uuid` no tiene FK ni tabla SQL de usuarios; los schemas validan almacenes y tracking.
- Los usuarios nuevos de TinyDB tienen UUID persistente; los registros antiguos reciben uno estable al primer acceso. El UUID se deriva de la identidad TinyDB, se guarda en TinyDB y no se expone en respuestas de auth.
- Añadido `uv run seed-inventory`, idempotente. Ejecutado contra Supabase: 6 SKUs, 5 entradas y 3 salidas; segunda ejecución: 0 duplicados. Saldos comprobados: `CLT-SNK-W-42` LA=63, `CLT-SNK-W-42-Z` ZGZ=40, `TEC-EAR-001` LA=80 y `CSM-SRM-030` ZGZ=93; otros SKUs en cero.
- El contexto HITO05 de esta tarea ya existe como `memory-bank/history/HITO05-CONTEXT.md`; se conservó sin crear un histórico duplicado. `CONTEXT.md` de raíz está vacío.
- Validaciones: pruebas focalizadas de auth (19 correctas); smoke HTTP del router, validación de reglas, seed SQLite idempotente/neto y migración de UUID legacy correctos; diagnósticos del editor y `git diff --check` limpios. La conexión PostgreSQL se verificó con la ejecución del seed; advertencia no bloqueante Starlette/httpx.
- Revisión del checklist: rutas/modelos/stock/configuración/seed remoto verificados. Gap pendiente: `inventory_seed.py` usa un UUID fijo para las órdenes sembradas, que no coincide con ningún usuario de TinyDB local; los POST de la API sí guardan el UUID autenticado.
## ERROR-HANDLING — proveedores e inventario — 2026-11-03
- Sustituidos los mensajes de respaldo con códigos HTTP por texto legible en `suppliers.ts` e `inventory.ts`. Los errores de servidor usan mensajes seguros sin reenviar su cuerpo; se conservan los mensajes de validación.
- Añadido botón Reintentar junto a errores de carga del directorio de proveedores, productos, selectores de productos en entradas/salidas e historial. Reutiliza consultas y filtros; limpia el error, restaura carga y no envía formularios. Eliminado el mensaje duplicado de carga en salidas.
- Validaciones: `npm --prefix uis/backoffice run build` correcto; diagnósticos del editor sin errores en los cuatro archivos. Comprobación en memoria con respuestas HTTP simuladas: 500/503/404 sin códigos visibles y validación 422 conservada en ambos wrappers. React/jsdom: las cinco vistas recuperan la carga al pulsar Reintentar, repiten la misma consulta sin POST y eliminan el error. El primer intento del script de comprobación falló al resolver una importación TypeScript; se corrigió el script, sin cambios adicionales en producción.
- Contexto raíz vacío conservado intacto y archivado íntegro en `memory-bank/history/ERROR-HANDLING-RETRY-CONTEXT-2026-11-03.md`. Antecedentes contrastados con el registro de auditoría de 2026-10-28. Sin cambios en reglas de negocio, backend, secretos ni históricos previos.
- Límite: no se ejecutó suite completa ni verificación visual en navegador; las interacciones se comprobaron con DOM y peticiones simuladas. Siguiente paso: revisión manual con backend disponible.

## HITO05 — inventario backoffice — 2026-10-27
- Añadida la capa de llamadas a `/inventory` con autenticación existente y vistas de productos, entradas, salidas e historial; protegidas por el guard global del backoffice.
- Conservado `memory-bank/history/HITO05-CONTEXT.md` sin sobrescribirlo. Archivada la versión vigente usada en `memory-bank/history/HITO05-INVENTORY-UI-CONTEXT-2026-10-27.md`; `cmp` confirma que coincide byte a byte con `CONTEXT.md`.
- Validaciones: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint`, correctos.
- Ajustado `inventoryRequest` para mostrar el cuerpo de error tanto en JSON como en texto plano; build y lint repetidos correctamente.
- Bloqueo: `services/api` no expone las rutas `/inventory`; la integración HTTP requiere un backend compatible disponible en la URL configurada. No se verificaron llamadas reales ni flujos en navegador.

## API-042 / FE-019 — cobertura backoffice y utilidades — 2026-10-27
- Actualizado `TESTING.md` con el plan, grupos `/suppliers` y `/api/incidents`, casos de éxito/límite/fallo y ejecución independiente de Jest. Añadidos `test_incidents.py` y fixture autenticado; la suite existente de proveedores se utiliza como segundo grupo.
- Documentado en `TESTING.md` un caso concreto identificado con apoyo de IA: rechazar tokens de recuperación como sesión y enlaces de reset reutilizados; ambos están cubiertos por pruebas existentes.
- Añadidos tests bajo `uis/backoffice/src/__tests__/` para sesión, `positiveRate`, `errorMessage` e `incidentError`. Extraídos los helpers puros a módulos pequeños y conservados sus exports desde las utilidades existentes. Excluidos los tests del tsconfig de producción y conservado su tsconfig Jest.
- Cobertura: `routes/suppliers.py` 100% (29 tests), `routes/incidents.py` 100% (4 tests), Jest 100% en `session.ts`, `supplierUtils.ts` e `incidentErrors.ts` (14 tests). Suite FastAPI completa: 66 tests correctos. Build y lint de backoffice correctos.
- Advertencia no bloqueante de deprecación Starlette/httpx. Archivos de cobertura excluidos e ignorados. Contexto archivado íntegro en `memory-bank/history/API-042-FE-019-CONTEXT.md`.

## AUTH-088 — pruebas de autenticación — 2026-10-27
- Añadido `TESTING.md` antes de escribir los tests con casos previstos de éxito, límites y fallos para registro, login, `/auth/me`, recuperación, reset, cambio y validación de tokens, además de comandos y justificación.
- Añadidos módulos pytest por endpoint bajo `services/api/tests/`, fixtures con TestClient y TinyDB temporal, y `pytest-cov` en el grupo dev del proyecto API. Eliminadas comprobaciones de OpenAPI/serialización del test de login anterior.
- Configurado Jest en `uis/backoffice`; aislados y probados los helpers de localStorage en `src/utils/session.ts`, manteniendo sus exports previos desde `auth.ts`.
- Validaciones: `uv run --directory services/api pytest --cov=routes.auth --cov-report=term-missing --cov-fail-under=70`: 62 correctas, 98,11% en `routes/auth.py`; `npm --prefix uis/backoffice test -- --coverage --runInBand`: 4 correctas, 100% en `session.ts`; build y lint de backoffice correctos. Una advertencia no bloqueante de deprecación Starlette/httpx.
- Contexto archivado íntegro en `memory-bank/history/AUTH-088-CONTEXT.md`. Sin cambios en lógica de autenticación de producción; el refactor separa únicamente helpers de sesión para probarlos con Jest.

## Skill — auditoría de gestión de errores — 2026-10-27
- Creada la skill invocable `.agents/skills/error-handling-audit/SKILL.md`, adaptada a la estructura actual del monorepo: TypeScript/React/Vite, FastAPI/Python, gateway Node.js y scripts Python. Define revisión de solo lectura, ocho categorías de hallazgo, criterios por tecnología, severidades y formato de informe; no ejecuta remediaciones.
- Validación: inspección del frontmatter `name`/`description`, ubicación y contenido de la skill. No se modificó código de aplicaciones ni se ejecutaron auditorías o pruebas.

## ERROR-HANDLING-AUDIT — remediación del feedback — 2026-10-28
- Añadido `docs/ERROR_HANDLING_AUDIT.md` con hallazgos demostrables, severidad, correcciones aplicadas y cobertura/límites en frontend, API/gateway y scripts.
- Backoffice: los fallos de red de `apiFetch` se traducen a mensajes legibles; la edición del perfil queda bloqueada si falla su carga y ofrece reintento; el flujo de cambio de contraseña libera `checking` en `finally`, no habilita el formulario sin sesión verificada y ofrece acceso al login; la descarga CSV muestra carga/éxito y permite reintento.
- Gateway/worker: el worker Python devuelve errores esperados como JSON; solo errores de validación reconocidos reciben 422. Fallos inesperados reciben 500 genérico, sin reenviar `stderr` ni detalles de excepción al cliente. Se contiene `EPIPE` si el worker cierra `stdin` antes de tiempo. El log de gateway evita imprimir el error crudo.
- CLI: `scripts/analyze.py` captura fallo de escritura de `results.csv`, comunica una instrucción operativa por `stderr` y devuelve código 2.
- Verificaciones: `npm --prefix uis/backoffice run build` correcto; `node --check services/api/src/server.js` correcto; parseo AST de los dos scripts Python correcto; petición real al gateway con CSV inválido devolvió 422 JSON sin traceback; worker sustituido por `/bin/false` devolvió 500 genérico sin derribar el gateway; fallo de escritura simulado en el CLI devolvió 2, mensaje conciso y sin traceback; `get_errors` sin errores en los archivos modificados.
- El histórico `memory-bank/history/ERROR-HANDLING-AUDIT-CONTEXT-2026-10-28.md` se creó inicialmente como copia exacta de `CONTEXT.md` y, a petición del usuario, se amplió con el contexto, cambios, hallazgos, verificaciones y límites de la tarea; actualmente funciona como registro de remediación y ya no coincide byte a byte con el archivo raíz. `CONTEXT.md`, vacío y modificado previamente por el usuario, se conservó intacto. Los históricos de backend/seed mencionados en registros previos no existen en este checkout.
- Límites: no se ejecutó suite completa ni prueba visual en navegador; no se modificó la lógica de negocio ni la skill original de solo lectura.

## AUTH-02 — autenticación frontend — 2026-10-27
- Integrados en `uis/backoffice` el registro (`POST /users` con campos opcionales de perfil, luego login automático), persistencia JWT en `localStorage`, vista de perfil (`GET /auth/me`, `PUT /profiles/me`) y guard de sesión para todas las vistas internas. Login, registro, recuperación y restablecimiento siguen siendo públicos; el website no fue modificado.
- Centralizado el uso Bearer y el manejo de 401 en las llamadas de autenticación, incidencias, proveedores y análisis/exportación CSV. El cierre de sesión elimina el token y redirige a `/login`; Vite reenvía `/users` y `/profiles` a FastAPI.
- Validaciones: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint` correctos; diagnósticos del editor sin errores. No se ejecutó prueba de navegador.
- El contexto se archivó íntegro en `memory-bank/history/AUTH02-CONTEXT.md`. Este checkout tiene un backoffice React/Vite; no hay fuentes ni paquete Next.js en `uis/talent-pipeline-tracker`, por lo que la integración se hizo en el frontend interno existente, sin crear otra aplicación.

## Históricos — consolidación del contexto de incidencias — 2026-10-27
- Eliminados los históricos duplicados `INCIDENTS-BACKEND-CONTEXT.md` y `INCIDENTS-SEED-CONTEXT.md`; ambos eran idénticos byte a byte a `INCIDENTS-FRONTEND-CONTEXT.md`, que se conserva como copia única del briefing. `INCIDENTS-MANAGER-MODEL-CONTEXT.md` se conserva porque su contenido es distinto.
- Verificación previa: SHA-256 idéntico en los tres duplicados; estado Git previo mostraba `CONTEXT.md` modificado por el usuario. No se modificó ese archivo.

## Gestor de incidencias — carga real del historico — 2026-10-22
- Ante el panel con metricas en cero, confirmada la base activa de FastAPI mediante sus archivos abiertos: `services/api/data/suppliers.json`. La tabla incidents estaba vacia; las cargas anteriores solo se habian realizado en bases temporales.
- Detenido el supervisor Node y confirmada la salida de sus procesos antes de escribir en TinyDB. Ejecutado el seed existente sobre la base real con ruta --db explicita: 100 filas leidas, 95 incidencias insertadas, 0 duplicados y 5 filas invalidas descartadas (4, 26, 43, 69 y 98). No se mostraron datos personales.
- Comprobacion inmediata de la base: total 95; estados open=29, resolved=52, discarded=14; categorias lost_parcel=14, carrier_issue=45, delivery_failure=19, returns_issue=17; origen customer=95; sedes la_office=50 y zaragoza_office=45. Todos los registros previos de todas las tablas conservados sin modificaciones.
- Backend reiniciado con `npm --prefix services/api run dev`: Node 8080 y FastAPI 8081 con startup completado. Frontend no modificado; siguiente paso del usuario: abrir Incidencias y pulsar Actualizar, iniciando sesion de nuevo si procede. La prueba visual sigue pendiente por las dependencias de Chromium.
- Contexto de negocio sin cambios respecto al historico `memory-bank/history/INCIDENTS-FRONTEND-CONTEXT.md`; se conserva la copia existente sin sobrescribir historicos. No se tocaron configuraciones secretas ni se creo commit.

## Gestor de incidencias — frontend — 2026-10-22
- Integradas vistas Registrar incidencia e Incidencias en el menu existente de `uis/backoffice`, manteniendo analizador CSV, proveedores y autenticacion. Nuevos componentes PascalCase en `src/app/incidents`: IncidentRegistration, IncidentDirectory e IncidentSummaryPanel; utilidades/tipos/catalogos/peticiones en `src/utils/incidents.ts`.
- Formulario con seis campos editables del modelo y tres metadatos de solo lectura no enviados. Branch siempre visible y obligatorio, cinco sedes con etiquetas exactas y Central incluido; resaltado cuando origin=branch. Validacion local de campos requeridos/textos en blanco, errores por campo, bloqueo sincrono de duplicados, carga y controles deshabilitados, reset y confirmacion de exito.
- Listado con filtros status/origin/branch, carga, error con reintento y mensajes de vacio o sin coincidencias. Detalle desplegable con descripcion/UUID/fechas UTC; selector con transiciones del contexto y estados finales bloqueados. Cambio optimista con rollback a la fila anterior y error local si falla; al completar se actualiza resumen y se elimina la fila si deja de cumplir el filtro de estado. Filtros y refresco bloqueados durante actualizaciones pendientes.
- Resumen por estado/categoria/origen/sede y total, con peticion, carga, error y reintento independientes del listado. Catalogos exactos del contexto, peticiones Bearer, sesion caducada limpiada y mensajes locales seguros en lugar de texto tecnico del backend. Etiquetas/fieldset/legend, aria-invalid/describedby, alert/status, foco visible, controles tactiles y utilidades Tailwind; tabla con scroll accesible horizontal.
- Verificaciones ejecutadas: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint` correctos. Corregida una advertencia set-state-in-effect del resumen derivando la carga por version de peticion; repetidos build/lint sin advertencias. Diagnosticos del editor sin errores en las cinco rutas tocadas.
- Comprobacion compilada en memoria con Vite y renderToStaticMarkup correcta: estados/origenes/sedes, nueve categorias, cuatro transiciones, seis campos y tres metadatos readonly, cinco opciones de sede/branch obligatorio, cargas iniciales independientes, Bearer, traduccion segura de errores 400 por campo, 500, red y 401 con limpieza de sesion. Primera configuracion virtual de la comprobacion no resolvio la entrada; corregida para usar archivo real, sin modificar fuentes ni crear tests.
- Bloqueo: intento de lanzar Chromium fallo por libatk-1.0.so.0 ausente. No se verificaron interacciones de navegador, envio/reset, errores por campo interactivos, rollback, filtros ni responsive visual/capturas. Instalar dependencias Linux de Chromium con permisos adecuados y validar estos flujos antes de aceptar visualmente. PageSpeed no medido sin URL publica. No se crearon archivos de pruebas ni se ejecuto la suite completa.
- README del backoffice actualizado. Contexto archivado integro en `memory-bank/history/INCIDENTS-FRONTEND-CONTEXT.md`; cmp confirma identidad byte a byte. Frontend existente disponible en `http://localhost:5174` (HTTP 200); requiere backend/gateway activo y sesion para usar los datos.
- Estado: implementacion del bloque frontend completada, comprobaciones de navegador pendientes por entorno. Sin cambios backend, base real ni secretos; sin commit. Siguiente paso: resolver el bloqueo visual o recibir el siguiente bloque del usuario.

## Gestor de incidencias — backend — 2026-10-22
- Implementados en `services/api/routes/incidents.py` los cinco endpoints autenticados: POST/GET `/api/incidents`, GET `/api/incidents/{id}`, PATCH `/api/incidents/{id}/status` y GET `/api/incidents/summary`. Registrados en FastAPI y conectados al gateway Node existente; analizador y exportacion conservan sus rutas.
- Reutiliza el modelo Incident y la tabla `incidents` del seed, con bloqueo compartido. Creacion con metadatos automaticos, filtros opcionales combinables status/origin/branch/category, detalle 404 y resumen completo con total y conteos por estado/categoria/origen/sede, incluidos ceros en base vacia. `/summary` se registra antes de `/{id}`.
- PATCH solo admite status; valida las cuatro transiciones del contexto, rechaza permanencias y cambios desde estados finales, y actualiza exclusivamente status/updated_at. Modelo de entrada `IncidentStatusUpdate` con campos extra prohibidos.
- Errores de validacion del gestor devuelven 400 con campo/mensaje claro y sin valores de entrada; transiciones invalidas 400 con campo status. Contrato 422 de otras rutas conservado. Manejador global de excepciones FastAPI devuelve 500 generico sin stack trace ni detalles internos. Gateway permite Authorization en CORS, devuelve 503 generico si falla el upstream y oculta mensajes internos en errores inesperados del analizador.
- Verificacion HTTP aislada con TestClient y base temporal correcta: 401 en cinco rutas sin sesion, listado/resumen vacios, campos obligatorios y valores invalidos 400, creacion 201, detalle 200/404, filtros validos combinados/invalidos, 16 combinaciones de transicion, inmutabilidad de otros campos, actualizacion de fecha, UUID/JSON invalidos 400, PATCH con campos extra 400, excepcion simulada 500 sin detalle privado y 422 anterior conservado. Primer intento detenido por email sintetico con dominio .test reservado; corregido solo el dato de validacion a example.com y repetido correctamente.
- Integracion ejecutada con Node real -> Uvicorn en puertos temporales y base sembrada: Bearer, listado/filtros, creacion/detalle/PATCH, 400, summary exacto de 95 registros (estados 29/0/52/14, carrier_issue 45, customer 95, sedes 50/45), analizador vacio 422, exportacion sin datos 404 y health 200. Procesos temporales cerrados al terminar.
- `PYTHONDONTWRITEBYTECODE=1 uv run --directory services/api pytest -q tests/test_auth.py`: 6 correctas, advertencia Starlette/httpx preexistente. `node --check services/api/src/server.js`: correcto. Diagnosticos del editor sin errores en main/modelos/router/gateway. No se crearon archivos nuevos de pruebas ni se ejecuto la suite completa.
- Documentados endpoints, errores, resumen y proxy en README de API. Contexto archivado integro en `memory-bank/history/INCIDENTS-BACKEND-CONTEXT.md`; `cmp` confirma identidad byte a byte. No se alteraron los historicos anteriores ni el contexto raiz del usuario.
- Estado: bloque backend completado. Sin cambios frontend, base real o secretos; sin commit. Siguiente paso: recibir el siguiente bloque del usuario.

## Gestor de incidencias — seed historico — 2026-10-22
- Creado `scripts/seed_incidents.py`, ejecutable con `uv run --project services/api python scripts/seed_incidents.py [csv_path] [--db ruta]`. CSV por defecto: `scripts/incidents-trackflow.csv`; misma base que la API mediante `database.open_database`, tabla separada `incidents`. La API debe estar detenida durante el seed.
- Extraidas las reglas originales, catalogos y lectura/validacion CSV a `packages/shared/incident_validation.py`; el analizador anterior las reutiliza conservando sus importaciones y resultados. Solo el seed permite la ausencia de `incident_id` para aplicar el fallback del briefing.
- Mapeos completos previos al insert: estados OPEN/CLOSED/DISCARDED, cinco categorias CSV a cuatro categorias del modelo, US/ES a la_office/zaragoza_office, descripcion literal, titulo de primeros 120 caracteres recortados, fecha a medianoche UTC y ambas fechas iguales. Origen fijo `customer`; validacion final mediante `Incident`.
- Idempotencia mediante UUID5 estable derivado de `incident_id`; no se almacena ese campo CSV en el modelo ni se necesita una tabla auxiliar. Si falta, se usa titulo + fecha de creacion. Los registros existentes no se sobrescriben. Las filas invalidas o no mapeables se descartan y se resumen al final solo con fila/motivos, sin correos ni contenido de filas.
- Validaciones ejecutadas con Python en memoria y bases temporales: analizador conserva 100/95/5 y CSAT 3.06; seed real dos veces inserta 95 y luego 0 (95 omitidas), descartando filas 4, 26, 43, 69 y 98. Estados 29/52/14, categorias 14/45/19/17, sedes 50/45, origen, fechas, campos del modelo y tablas ajenas conservadas.
- Verificada CLI desde otro directorio con CSV por defecto y --db temporal, sin emails en stdout/stderr; fallback sin ID, duplicados internos, BOM, descripcion literal, titulo 120, rechazo de titulo vacio tras transformacion, ID estable aunque cambie descripcion y error de encabezados con codigo 2 antes de abrir la base. El analizador sigue rechazando CSV sin cabecera incident_id.
- Documentados comandos, mapeos, idempotencia, descartes y limitacion de concurrencia en ambos README de scripts. No se ejecuto el seed sobre la base real ni se crearon archivos nuevos de pruebas; no se ejecuto la suite completa.
- Archivado el contexto vigente en `memory-bank/history/INCIDENTS-SEED-CONTEXT.md`; `cmp` confirma copia exacta byte a byte. No se modificaron el contexto raiz del usuario ni el historico anterior del bloque modelo.
- Estado: bloque seed completado; endpoints y frontend quedan fuera de este bloque. Siguiente paso: recibir el siguiente bloque del usuario. Sin cambios en secretos ni otras rutas protegidas; sin commit.

## Gestor de incidencias — bloque modelo — 2026-10-22
- Implementado exclusivamente el bloque de modelo de datos solicitado en `services/api/models.py`, reutilizando Pydantic y sin modificar el analizador de CSV anterior ni la autenticacion.
- `IncidentCreate` requiere titulo, descripcion, categoria, origen y sede. Rechaza campos extra, textos vacios o compuestos solo por espacios, y valores ajenos a los catalogos del contexto. Conserva literalmente la descripcion para el futuro seed.
- Catalogos: nueve categorias exactas del briefing, estados `open`/`in_progress`/`resolved`/`discarded`, origenes `customer`/`branch`/`internal` y cinco sedes exactas. `branch` es obligatorio para todos los origenes; se debe indicar `central` cuando no corresponda una sede especifica. Estado inicial por defecto `open`.
- `Incident` genera UUID4 y fechas UTC; `updated_at` comienza igual a `created_at` y se actualiza automaticamente en asignaciones validadas de campos de negocio. ID y fecha de creacion no se pueden reasignar. Las fechas requieren zona horaria. El esquema de entrada no acepta ID ni fechas del cliente.
- Validacion ejecutada con `uv run --directory services/api python -` y datos sinteticos en memoria: UUID distintos, fechas UTC, campos obligatorios, valores invalidos, 135 combinaciones categoria/origen/sede, actualizacion automatica, rechazo de asignacion de estado invalido sin mutacion, metadatos protegidos, fechas sin zona rechazadas y round-trip JSON correctos. No se crearon archivos de pruebas ni se ejecuto la suite completa.
- Contexto de este bloque archivado integro en `memory-bank/history/INCIDENTS-MANAGER-MODEL-CONTEXT.md`, sin cambiar el `CONTEXT.md` actualizado por el usuario.
- Estado: bloque de modelo completado; sin endpoints, persistencia, seed, transiciones de ciclo de vida ni cambios frontend. Siguiente paso: recibir el siguiente bloque del usuario. Sin cambios en secretos ni otras rutas protegidas; sin commit.

## AUTH-03 — 2026-10-22
- Implementados `POST /auth/forgot-password`, `/auth/reset-password` y `/auth/change-password` sobre FastAPI/TinyDB, conforme al `CONTEXT.md` actualizado (Resend).
- Recuperacion con respuesta 200 uniforme para emails validos existentes o desconocidos, envio en segundo plano por API HTTPS de Resend, plantilla HTML movil y texto plano. Los errores del proveedor no revelan la existencia de cuentas.
- JWT de recuperacion con proposito separado y expiracion de 30 minutos; solo el SHA-256 pendiente se persiste en el usuario. Escritura de contrasena bcrypt y consumo del token bajo bloqueo, en una unica actualizacion. Emitir otro token o cambiar la contrasena invalida el anterior; no se revocan los JWT de sesion previos.
- Frontend React/Vite: login minimo (no existia), enlace de recuperacion, `/forgot-password`, `/reset-password` y `/account/change-password`. Incluye confirmacion, bloqueo de duplicados, errores accesibles, verificacion de sesion y redireccion con exito. Bearer en proveedores, sesion por pestana y cierre de sesion.
- Documentadas `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `PASSWORD_RESET_URL`, restricciones del proveedor, proxy `/auth` y comandos Codespaces. `.gitignore` ahora excluye `.env` y `.env.*` en cualquier carpeta; no se leyo ni modifico el `.env` del usuario.
- Validaciones ejecutadas: `uv run --directory services/api pytest -q tests/test_auth.py`: 6 correctas (advertencia Starlette/httpx preexistente). Comprobacion HTTP aislada con TestClient/base temporal y correo simulado: respuesta uniforme, reset correcto, segundo uso 400, token invalido/expirado 400, token de recuperacion rechazado como sesion, sesion rechazada como recuperacion, cambio sin sesion 401, contrasena actual incorrecta 400 y login con nueva contrasena correcto. Payload Resend con HTML/texto verificado mediante transporte simulado. Comprobacion adicional: token vacio 400 y texto uniforme exacto.
- Frontend: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint`: correctos. Diagnosticos del editor sin errores en los archivos nuevos y modelos/rutas tocados. No se crearon archivos nuevos de pruebas ni se ejecuto la suite completa.
- Entorno: se instalaron `uv`, dependencias Python/frontend y Chromium para poder verificar. Primera compilacion bloqueada por dependencias ausentes; correcta tras `npm install`.
- Bloqueos: Chromium no arranca por falta de `libatk-1.0.so.0`; no se ejecutaron las interacciones, capturas ni la comprobacion responsive visual. Falta entrega real de correo con clave/remitente Resend validos y URL publica configurada. PageSpeed no medido sin URL publica. No se accedio a secretos ni se cambiaron rutas protegidas fuera del progreso y del nuevo archivo historico autorizado.
- Frontend local iniciado en `http://localhost:5174`; requiere la API FastAPI en 8081 y configuracion de correo para el flujo real. No se creo commit.
- Contexto activo archivado integro en `memory-bank/history/AUTH03-CONTEXT.md`; se conserva el contexto raiz modificado por el usuario y el historico AUTH-01.
- Siguiente paso: configurar variables privadamente, iniciar FastAPI, instalar dependencias Linux de Chromium con permisos adecuados y validar recepcion real del correo, flujo de navegador y responsive. Rate limiting y auditoria opcionales no implementados.

## Estado general
- Actualizacion 2026-10-15: implementado AUTH-01 (Hito 10) sobre la API FastAPI/TinyDB; suite backend completa con 35 pruebas correctas.
- El `CONTEXT.md` AUTH-01 activo se archivo integro en `memory-bank/history/HITO10-CONTEXT.md`; no se modifico el contexto de raiz.
- AUTH-01 implementa JWT stateless, hash bcrypt con libpass, usuarios y perfiles en TinyDB, y protege las seis rutas de proveedores.
- La verificacion automatica cubre `/docs`/OpenAPI, registro-login-Bearer, 401/403 y JWT expirado. La secuencia visual manual en Swagger aun no se ejecuto.
- La clave real no se crea ni se versiona en este cambio; `services/api/README.md` documenta generacion local de `.env`, excluido de Git.
- Actualizacion 2026-10-08: implementado el Hito 09, directorio de proveedores de TrackFlow, conforme al contexto archivado y la rubrica de entrega.
- Backend y frontend funcionales; backend, persistencia real, integracion HTTP, build y lint verificados. Verificacion visual de navegador pendiente por bibliotecas del contenedor.
- Contexto del Hito 09 archivado integro en `memory-bank/history/HITO09-CONTEXT.md`; el original de raiz se conserva.
- Se mantiene el analizador de incidencias anterior, cuyo contexto esta archivado en `memory-bank/history/INCIDENTS-ANALYSIS-CONTEXT.md`.
- Regla permanente de archivo y progreso incorporada en `AGENTS.md`, independiente de commits, con autorizacion del desarrollador para estas actualizaciones.

## Protocolo para nuevas funciones
- Antes de implementar o anadir cualquier funcion, leer este `progress.md` y revisar los contextos de `memory-bank/history/` para conocer el estado, las decisiones y las funciones existentes.
- Contrastar esos antecedentes con el `CONTEXT.md` activo; no inventar requisitos ni duplicar implementaciones existentes.
- Al cerrar cada hito, archivar una copia integra del contexto en `memory-bank/history/` y actualizar este archivo, incluso sin commit. Preservar historicos existentes y registrar expresamente pruebas pendientes o bloqueadas.

## Completado
- Hito 10 - AUTH-01:
  - Modelos `User` con email, contraseña hasheada, estado, rol enum y fecha; `Profile` independiente, 1:1 por `user_id`. El modelo User no incluye nombre ni contacto.
  - Servicios TinyDB para crear/buscar/listar/actualizar/eliminar usuarios y leer/actualizar perfiles; el registro crea el perfil asociado y la eliminación lo borra.
  - `/users` ofrece registro público con rol fijo `user`; CRUD protegido con reglas de dueño/admin y modificación de roles limitada a admin.
  - `/auth/login` emite JWT firmado, con expiración `ACCESS_TOKEN_EXPIRE_MINUTES`; `/auth/me` incluye perfil. Bcrypt usa `libpass[bcrypt]` y `from passlib.hash import bcrypt`.
  - `/profiles/me` solo opera sobre el perfil propio. `get_current_user` valida Bearer, firma, expiración y existencia/estado del usuario; tokens ausentes, inválidos y expirados reciben 401, recursos ajenos 403.
  - Los seis endpoints existentes de `/suppliers` ahora requieren JWT. OAuth2 bearer y HTTP bearer están publicados en OpenAPI para usar `/docs` y pegar el token.
  - Clave tomada desde `services/api/.env`, nunca definida en código; el archivo se ignora en Git. `README.md` documenta crearla con `openssl rand` y ejecutar el servicio.
  - Se archivó el contexto activo completo como `memory-bank/history/HITO10-CONTEXT.md`; `AGENTS.md` explicita que debe hacerse incluso si se indica que el contexto ya está actualizado.
- Hito 09 - directorio de proveedores:
  - Estructura de entrega: `services/api/main.py`, `models.py`, `database.py`, `routes/suppliers.py` y `seed.py`.
  - Modelos Pydantic separados de entrada/respuesta con los campos exactos, categorias del contexto, estados `active`/`suspended`, tarifa positiva y finita, moneda por pais y `updated_at` UTC generado por el sistema.
  - CRUD `/suppliers`, filtros combinados por pais/categoria, actualizacion de tarifa con timestamp y errores 404/422. ID generado por TinyDB.
  - Seeder ejecutable con `uv run seed`: 15 proveedores exactos, comprobacion previa por nombre/pais, sin duplicados ni sobrescritura de tarifas modificadas; informa del numero de inserciones.
  - Persistencia TinyDB compartida por API y seeder; bloqueo dentro del proceso, una instancia/worker y seeder con API detenida.
  - API Node existente conserva incidencias y redirige `/suppliers` a FastAPI; arranque conjunto con `npm run dev`.
  - Pagina en `uis/backoffice/src/app/suppliers/SupplierDirectory.tsx`, accesible desde el menu: listado API, filtros sin recarga, formulario completo con errores, tarifas editables y controles de suspension/reactivacion con estados diferenciados.
  - Se mantiene `uis/backoffice` como aplicacion interna React/Vite; no se duplica en `uis/application` ni se migra a Next.js.
  - Comandos de instalacion, seeder, ejecucion y pruebas documentados en los README de API y backoffice.
  - Contexto archivado y regla permanente actualizada por peticion explicita del desarrollador.
- Fase 1 - analisis CSV local:
  - Creada la CLI `scripts/analyze.py` y el modulo reutilizable `scripts/incidents_analysis.py` con validacion del esquema, clasificacion de errores, metricas, CSAT y exportacion a `results.csv`.
  - El archivo de referencia `incidents-trackflow.csv` se compara con los resultados declarados en `CONTEXT.md`; las discrepancias detienen el proceso.
  - Validado mediante fixture sintetico anonimizado con los conteos de referencia: 100 filas, 95 validas, 5 invalidas y CSAT 3.06. La exportacion se comprobo sin correos.
  - Confirmado que `satisfaction_score` puede omitirse del encabezado; los registros `CLOSED` sin puntuacion se clasifican como invalidos.
  - Validacion final con el CSV real: coincidencia exacta con totales, categorias, estados, paises, cinco reglas de error y CSAT declarados en `CONTEXT.md`.
- Fase 2 - integracion en plataforma:
  - La API expone `POST /api/incidents/analyze` (multipart/form-data) y `GET /api/incidents/results/export`, reutiliza el procesador Python y responde con errores descriptivos.
  - El backoffice ofrece la vista navegable de analisis, carga por selector/drag-and-drop, metricas, desgloses, errores de validacion y descarga CSV.
  - Documentados los comandos y configurado proxy `/api` de Vite hacia el servicio local.
  - Prueba web end-to-end con el CSV real: reporte y descarga correctos; la respuesta y el CSV no contienen emails.
- Analisis del contexto TrackFlow (operacion en Los Angeles y Zaragoza, retos de inventario, carrier management, devoluciones y CX).
- Revision de estructura actual del monorepo y convenciones de carpetas `uis/` y `services/`.
- Creacion de `memory-bank/` con:
  - `projectbrief.md`
  - `techContext.md`
  - `progress.md` (este archivo)
- Creacion de `AGENTS.md` con:
  - Archivos obligatorios de lectura al inicio de sesion.
  - Flujo obligatorio pre-commit (revision, validaciones, reglas, actualizacion de progreso y commit).
  - Rutas protegidas que no deben tocarse sin confirmacion explicita.
- Creacion de `.agents/rules/component-naming-convention.md` con alcance siempre activo para `uis/website/**` y `uis/backoffice/**`.
- Creacion de skill reusable en `.agents/skills/create-reusable-ui-component/SKILL.md` con objetivo, inputs y criterios verificables.
- Inicializacion y personalizacion de `uis/website`:
  - React + TypeScript + Vite + Tailwind.
  - Web corporativa completa en `/` (mision, vision, servicios, equipo, contacto).
  - Formulario funcional con validaciones y boton de limpiar.
  - Metadatos SEO y JSON-LD en `index.html`.
- Inicializacion y personalizacion de `uis/backoffice`:
  - Layout interno independiente.
  - Vista de entrada con KPIs y alertas operativas visibles en pantalla.
- Creacion de `services/api` con endpoint `GET /health` y README operativo.

## En progreso
- Hito 10: pendiente abrir `/docs` en navegador y ejecutar allí el registro-login-Authorize manual; el flujo HTTP, los esquemas OpenAPI, errores 401/403 y expiración ya están cubiertos por pruebas.
- Hito 09: pendiente completar la verificacion visual/funcional en Chromium (desktop y movil) tras instalar las dependencias de sistema del navegador.

## Validaciones ejecutadas
- Hito 10: `cd services/api && uv run pytest -q`: 35 pruebas correctas; una advertencia preexistente de Starlette/httpx.
- Hito 10: `uv run pytest -q tests/test_auth.py`: 6 pruebas correctas, incluyendo registro, hash bcrypt, login JSON/formulario OAuth2, roles, permisos, perfil, JWT inválido/expirado y esquema OpenAPI.
- Hito 10: persistencia de proveedores validada con autorización Bearer a través de reinicio real de Uvicorn.
- Hito 10: compilación Python y registro de rutas en OpenAPI validados; import de `jose.jwt`, `dotenv.load_dotenv` y `passlib.hash.bcrypt` validado.
- Hito 09: `cd services/api && uv run pytest -q --tb=short`: 29 pruebas correctas; incluye comparacion literal de seeder/categorias/estados con `CONTEXT.md` y reinicio de un proceso Uvicorn real con recuperacion de los datos.
- Hito 09: `uv run seed` dos veces sobre base temporal nueva: `Proveedores insertados: 15` y `Proveedores insertados: 0`. Base existente: 0 y 0.
- Hito 09: `cd uis/backoffice && npm run test:e2e -- --grep 'API integrada'`: 1 prueba correcta a traves de Vite/Node/FastAPI, con validaciones, CRUD y conservacion del endpoint de incidencias.
- Hito 09: `npm run build` y `npm run lint` en `uis/backoffice`: correctos despues de reorganizar la pagina.
- Hito 09: prueba visual Playwright preparada (filtros, alta, validacion cliente, errores API, tarifas, estados, desktop/movil); no se considera aprobada porque Chromium no inicia sin `libatk-1.0.so.0`.
- `npm run typecheck` en raiz: OK (tras instalar dependencias raiz con `npm install`).
- `npm run build` en `uis/website`: OK.
- `npm run build` en `uis/backoffice`: OK.
- Smoke test backend: `GET /health` responde `status: ok`.
- `python3 scripts/analyze.py` con fixture sintetico anonimo: coincidencia de referencias (100 total, 95 validos, 5 invalidos, CSAT 3.06); exportacion sin correos.
- `python3 scripts/analyze.py scripts/incidents-trackflow.csv`: OK, resultados coinciden con el contexto; filas invalidas 4, 26, 43, 69 y 98, sin exposicion de emails.
- Prueba API multipart y exportacion: OK; CSV vacio devuelve 422 y extension incorrecta 400.
- Prueba end-to-end a traves de Vite con CSV real: carga, metricas y descarga OK; respuesta y CSV sin emails.
- `npm --prefix uis/backoffice run build`: OK.

## Guía de arranque de servicios — 2026-11-15
- Creada [docs/GUIA-LEVANTAR-SERVICIOS.md](../docs/GUIA-LEVANTAR-SERVICIOS.md) con comandos para Docker Compose y ejecución manual.
- Incluye preparación de `.env`, arranque inicial y posteriores, health checks, logs, URLs locales, seed y apagado seguro sin borrar volúmenes.
- Validación: `git diff --check -- docs/GUIA-LEVANTAR-SERVICIOS.md` correcta.
- El contexto usado se archivó en `memory-bank/history/SERVICES-STARTUP-GUIDE-CONTEXT-2026-11-15.md` y se verificó con `cmp`.

## Corrección de proxies de backoffice — 2026-11-15
- El proxy de `/inventory` dejó de usar `localhost:8000` por defecto y apunta a FastAPI en `127.0.0.1:8081`.
- Añadido el proxy `/telemetry` hacia FastAPI y fallback frontend a `/telemetry/events`.
- Documentada la limpieza de variables `.env.local` antiguas que todavía apunten a `localhost:8000`.
- Validación: `npm --prefix uis/backoffice run build` correcta; `git diff --check` correcto; `GET /inventory/products` llega a FastAPI y devuelve `401` sin credenciales, en lugar de `502`.

## Proximos pasos
- Hito 10: generar `services/api/.env` siguiendo el comando de `services/api/README.md` y completar la comprobacion manual visual de `/docs`.
- Instalar bibliotecas Linux de Chromium con `npx playwright install-deps chromium` desde una terminal con permisos adecuados y ejecutar `npm run test:e2e` en el backoffice con ambos servicios activos.
- Revisar por separado la vulnerabilidad alta preexistente de `source-map-js@1.2.1`, detectada por npm audit; no se ha aplicado una actualizacion ajena al alcance.

## Riesgos / bloqueos conocidos
- Aun no existe pipeline CI del monorepo para validar todos los subproyectos de forma unificada.
- Captura automatica con Playwright bloqueada en este entorno por dependencia de sistema faltante (`libatk-1.0.so.0`).
- TinyDB requiere una unica instancia/worker; no admite escrituras multiproceso seguras ni seeder concurrente con la API.
- Dependencia preexistente `source-map-js@1.2.1` con aviso de seguridad alto. Starlette TestClient muestra una advertencia de deprecacion de httpx, sin fallos de pruebas.

## Data Pipelines — implementacion fase 1, flows y tasks — 2026-11-16
- Implementado `mock_data/pipelines/pipeline.py` (ruta equivalente a `data/pipelines/` en este checkout), con el flow `weekly_warehouse_client_performance_flow` y tasks independientes de extraccion, transformacion y carga.
- Extraccion limitada a los cuatro eventos de negocio y a una semana ISO UTC, en transaccion PostgreSQL de solo lectura. Transformacion valida dimensiones/cantidades, deduplica por `event_id` y calcula los cinco campos KPI. La carga reconstruye la semana con lock, borrado y upsert en una transaccion atomica.
- Tasks con cache de Prefect de una hora; extraccion y carga reintentan errores transitorios de base de datos. El flow se puede invocar desde CLI con `python mock_data/pipelines/pipeline.py [--week-start YYYY-MM-DD] [--trigger manual|scheduled]`.
- El snapshot JSON de evaluacion es opcional y se invoca con `return_state=True`; un fallo simulado de esa task dejo el flow principal en estado Completed.
- Validacion: sintaxis AST correcta; `--help` correcto; smoke test de transformacion correcto (deduplicacion, agregacion y tasa cero); prueba de aislamiento del fallo opcional correcta. Prefect 3 y Pandas se instalaron solo en un venv temporal para estas comprobaciones.
- Bloqueos: no se ejecuto contra una base real por falta de credenciales/conexion. `services/api/pyproject.toml` esta restringido por la politica de contenido, por lo que no se pudo ejecutar `uv add "prefect>=3"` ni dejar declarada la dependencia; no se intento acceder por otra via. Configuracion esperada: `REPORTING_READ_DATABASE_URL` y `REPORTING_WRITE_DATABASE_URL`.
- Archivado el contexto utilizado en `memory-bank/history/HITO-DATA-PIPELINES-PHASE1-FLOWS-CONTEXT-2026-11-16.md`; `cmp` confirmo que coincide con `CONTEXT.md`.

## Data Pipelines — implementacion fase 2, resiliencia — 2026-11-16
- Confirmados retries en las dos tasks con acceso a base de datos (`extract_telemetry_events` y `load_weekly_performance`): 3 reintentos, espera de 10 s y filtro para reintentar solo errores transitorios. Se agregaron comentarios que justifican el limite de intentos y la espera.
- Documentada la clave de cache de `transform_weekly_performance`: `task_input_hash` incluye el lote de eventos y `week_start`; expiracion de una hora.
- El flow ya maneja explicitamente el fallo de la task opcional con `return_state=True`, inspecciona `is_failed()` y registra una advertencia sin detener la carga principal.
- Validacion estructural AST de retries, espera, cache, manejo de estado y comentarios: OK. Las pruebas funcionales de transformacion y aislamiento de snapshot siguen siendo las ejecutadas en fase 1; esta fase solo cambio comentarios.
- Archivado el contexto utilizado en `memory-bank/history/HITO-DATA-PIPELINES-PHASE2-RESILIENCE-CONTEXT-2026-11-16.md`; `cmp` confirmo que coincide con `CONTEXT.md`.

## Data Pipelines — implementacion fase 3, idempotencia — 2026-11-16
- La carga usa staging temporal y advisory lock por `week_start`; aplica `ON CONFLICT (warehouse, client_id, week_start) DO UPDATE` y solo actualiza cuando cambian los KPI. Elimina combinaciones obsoletas de la semana dentro de la misma transaccion. Repetir datos identicos conserva filas, IDs y `computed_at`.
- Nueva migracion `supabase/migrations/20261116170800_create_reporting_pipeline_tables.sql` para crear el esquema/tablas de reporting, incluida la clave unica de negocio y `reporting.pipeline_runs`.
- Cada corrida persiste inicio, fin, semana, trigger, fase, contadores, estado y error sanitizado. Los fallos de carga con resultado de commit incierto se marcan `unknown`; los demas fallos se registran como `failed` y se vuelven a lanzar.
- Validacion estructural y de sintaxis AST: OK. No se pudo ejecutar integracion SQL real: `psql` no esta disponible y no hay `REPORTING_*_DATABASE_URL` configuradas.
- Archivado el contexto utilizado en `memory-bank/history/HITO-DATA-PIPELINES-PHASE3-IDEMPOTENCY-CONTEXT-2026-11-16.md`; `cmp` confirmo que coincide con `CONTEXT.md`.

## Regla de archivo de contexto sin duplicados — 2026-11-16
- Añadida `.agents/rules/context-history-archiving.md`: comparar el contexto activo con el historico correspondiente; crear una copia solo si difiere, nunca sobrescribir y actualizar progreso siempre.
- `cmp` confirmo que `CONTEXT.md` coincide con el archivo archivado de fase 3; no se creo otra copia historica.

## Data Pipelines — implementacion fase 4, ejecucion CLI — 2026-11-16
- Confirmado que `mock_data/pipelines/pipeline.py` tiene guard `if __name__ == "__main__"` que invoca `main()` y el flow principal.
- Documentada en `mock_data/pipelines/PIPELINE_DESIGN.md` la frecuencia semanal de lunes por la mañana, los limites ISO en UTC, el comando CLI normal y el de reproceso manual.
- Validacion estatica del entrypoint y documentacion: OK. Se intento `python mock_data/pipelines/pipeline.py`, pero el entorno no tiene Pandas (`ModuleNotFoundError: No module named 'pandas'`); tambien faltan Prefect y las dos variables de conexion. No se pudo verificar una corrida completa ni se escribio en una base.
- `CONTEXT.md` habia cambiado desde el archivo de fase 3; se archivo esta revision en `memory-bank/history/HITO-DATA-PIPELINES-PHASE4-EXECUTION-CONTEXT-2026-11-16.md` y se verifico con `cmp`.

## Data Pipelines — implementacion fase 5, endpoints de reporting — 2026-11-16
- Añadidos `services/reporting/routes.py`, `schemas.py` y helpers en `mock_data/pipelines/queries.py` y `runs.py` para consultar la ultima corrida, disparar el flow manualmente y devolver KPIs semanales con el contrato del briefing. Las rutas usan `get_current_user`; el POST devuelve `202`, conserva el `run_id` asignado y ejecuta el flow con `BackgroundTasks`.
- Montado el router `/reporting` desde `services/api/main.py`. La raiz del repositorio se incorpora a `sys.path` dentro de `create_app()` antes de importar el modulo reporting, para resolver `mock_data/` sin afectar los imports existentes del API.
- El POST importa de forma diferida el flow `weekly_warehouse_client_performance_flow` y usa `resolve_week_start`; la consulta de KPIs mantiene el grano warehouse + client_id + week_start y los siete campos definidos por el contexto.
- Validacion: parseo AST de los modulos Python modificados y smoke test de FastAPI con DB/flow simulados: GET de corrida, contrato KPI, POST manual/run_id y rechazo de semana que no inicia en lunes pasaron; se verifico la dependencia `get_current_user`.
- Bloqueos: no se pudo validar arranque integral ni PostgreSQL real porque este entorno carece de Pandas, Prefect y URLs de conexion; la migracion de reporting debe aplicarse antes de usar los endpoints contra una base. La primera ejecucion del smoke test fallo por una implementacion incompleta del fake de DB; corregido el fake, la validacion paso.
- `CONTEXT.md` coincide con la copia de fase 4 (`cmp`); no se creo un archivo historico duplicado. No se modificaron `services/telemetry/analysis.py` ni `GET /telemetry/report`.
- Estado: fase 5 implementada; pendiente validar con dependencias y base de datos configuradas y comprobar el arranque real de FastAPI.

## Revisión del checklist de data pipelines — 2026-11-16
- La implementación cubre el flow ETL con tasks de extracción, transformación, carga y registro; reintentos justificados; caché de transformación; snapshot opcional con `return_state=True`; upsert por la clave única del briefing y registro de inicio/fin, estado, contadores y error. El contrato de KPIs y los tres endpoints de reporting coinciden estructuralmente con el contexto.
- El código vive en `mock_data/pipelines/`, ruta equivalente documentada a `data/pipelines/`; no se modificó `services/telemetry/analysis.py` y el diff de ese archivo está vacío.
- Se intentó `python mock_data/pipelines/pipeline.py`; la ejecución se detuvo al importar Pandas (`ModuleNotFoundError`). Prefect y las URLs `REPORTING_READ_DATABASE_URL`/`REPORTING_WRITE_DATABASE_URL` tampoco están disponibles; por tanto, la corrida completa y la idempotencia contra PostgreSQL no quedan verificadas en este entorno.
- Diferencias frente al diseño por resolver o aclarar: no se persiste el identificador de corrida propio de Prefect junto al `run_id` de reporting; las corridas concurrentes se serializan en la carga pero no se marcan `skipped`/`already_running`; el flow no acepta `late_event` aunque el diseño y la tabla lo enumeran. La configuración usa variables de entorno, mientras otra sección del diseño propone Prefect Blocks.
- No se creó otro histórico: el contexto activo coincide con la copia archivada de fase 4.

## Resolucion de diferencias del pipeline — 2026-11-16
- `reporting.pipeline_runs` ahora conserva el `prefect_run_id` nativo además del `run_id` de reporting; la respuesta de última corrida lo expone. Se añadió una migración compatible con la tabla existente.
- El flow adquiere un advisory lock de sesión no bloqueante antes del ETL. Una corrida solapada queda registrada como `skipped`/`already_running`; la carga conserva su lock transaccional. La migración amplía el estado permitido y la respuesta API reconoce `skipped`.
- `late_event` está admitido por el flow y el CLI. El diseño aclara que Prefect Secrets/Blocks inyectan las variables de entorno que consume la implementación.
- Validacion: AST, compatibilidad de la firma del flow, smoke test del lock con conexiones simuladas (acquire/skip/release), smoke test del flow solapado/`late_event`, endpoints y contrato KPI: OK. Pylance no resolvio Prefect y señaló una llamada incompatible a `record_pipeline_run(..., return_state=True)`; no se pudo confirmar con el runtime ausente si es un falso positivo del decorador. `git diff --check`: OK.
- Bloqueo restante: no hay Pandas, Prefect ni URLs PostgreSQL en el entorno; no se pudo ejecutar el ETL completo ni aplicar/probar la migración contra PostgreSQL. No se modificó `services/telemetry/analysis.py` ni se archivó otra copia de `CONTEXT.md`.

## Verificacion de integración aislada — 2026-11-16
- Se creó un PostgreSQL 16 desechable y un virtualenv temporal con Prefect 3.9.3, Pandas 3.1.1, SQLAlchemy, psycopg2 y SQLModel. Se aplicaron las migraciones de telemetría y reporting, incluida la nueva migración de `prefect_run_id`/`skipped`.
- Con cinco eventos sintéticos, el flow calculó `inbound=125`, `outbound=2`, `stockout=1`, `discrepancy=1` y `discrepancy_rate=0.5`. Una segunda corrida forzó una nueva carga y conservó una fila, el mismo `id` y `computed_at`; la corrida solapada se registró `skipped`/`already_running`.
- `python mock_data/pipelines/pipeline.py` terminó `Completed` contra el PostgreSQL de prueba y registró timestamps, contadores, estado y `prefect_run_id`. Los helpers de reporting y sus schemas leyeron los datos reales; el POST del router lanzó el flow y la consulta posterior devolvió `succeeded`.
- El endpoint se probó con la dependencia de autenticación reemplazada para el test; la integración de identidad real no formó parte de esta prueba. Al detener el servidor Prefect efímero desde TestClient aparecieron logs de cancelación de su recorder, aunque la corrida, las respuestas HTTP y las aserciones completaron correctamente.
- Se eliminaron el contenedor, el virtualenv, el estado temporal de Prefect y los snapshots de prueba. No se usó ni modificó una base externa. Para ejecutar con el Python normal del workspace todavía hay que instalar/declarar Prefect y Pandas en el entorno permitido y configurar las URLs reales; el `pyproject.toml` del API continúa restringido.

## Verificacion del manejo de fallos con Prefect — 2026-11-16
- Prueba aislada con Prefect 3 y tareas simuladas: falló la transformación y también el registro de metadata del estado fallido. `record_pipeline_run(..., return_state=True)` devolvió el estado de fallo sin ocultar la excepción original; el flow la volvió a lanzar y el proceso de verificación terminó con las aserciones aprobadas.
- Esto confirma en runtime la compatibilidad del argumento `return_state` en la rama que el intérprete Pylance del workspace no podía analizar por no tener Prefect instalado. Al apagar el servidor Prefect temporal hubo logs de cancelación de su recorder; no afectaron la prueba y el código de salida fue cero.
- Se limpiaron el estado y la caché temporal usados por esta prueba. `git diff --check`: OK.

## Instalacion de dependencias del pipeline — 2026-11-16
- En el intérprete activo `/home/codespace/.python/current/bin/python3` (Python 3.14.2) se instalaron `prefect==3.9.3` y `pandas==3.1.1`; SQLAlchemy, psycopg2 y SQLModel ya estaban instalados. No se modificaron manifiestos del proyecto.
- Verificación: import de las cinco dependencias e `python mock_data/pipelines/pipeline.py --help`: OK.
- No se ejecutó el ETL real: `REPORTING_READ_DATABASE_URL` y `REPORTING_WRITE_DATABASE_URL` no están configuradas en este entorno.
