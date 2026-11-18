# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## Análisis local de incidencias

Requiere Python 3.10 o posterior y usa únicamente la biblioteca estándar. Desde la raíz del monorepo:

```bash
python3 scripts/analyze.py incidents-trackflow.csv
```

Valida los campos y catálogos definidos en `CONTEXT.md`, excluye del análisis los registros inválidos, muestra sus motivos sin exponer emails y solicita confirmación antes de exportar `results.csv`. Cuando el archivo se llama `incidents-trackflow.csv`, contrasta los resultados con las cifras de referencia del contexto y detiene la ejecución si no coinciden.

## Seed de incidencias históricas

Requiere las dependencias Python de la API. Desde la raíz del monorepo, **con la API detenida**:

```bash
uv sync --project services/api
uv run --project services/api python scripts/seed_incidents.py
```

Por defecto lee `scripts/incidents-trackflow.csv` y usa la misma base TinyDB que la API (`SUPPLIERS_DB_PATH` o `services/api/data/suppliers.json`). Inserta exclusivamente en la tabla `incidents`; no modifica proveedores, usuarios ni perfiles. TinyDB no permite ejecutar este seed simultáneamente con la API ni con otro proceso que escriba en la misma base.

Para indicar otro CSV o una base de prueba:

```bash
uv run --project services/api python scripts/seed_incidents.py ruta/al/archivo.csv --db /tmp/trackflow-incidents.json
```

El analizador y el seed reutilizan `packages/shared/incident_validation.py`. Se descartan las filas inválidas antes del insert y se muestran al final sus números de fila y motivos, nunca los emails ni el contenido de las filas. Se validan también las transformaciones contra el modelo `Incident`.

Los mapeos del briefing convierten estados, categorías y país a sede (`US` → `la_office`, `ES` → `zaragoza_office`); `origin` siempre es `customer`. El título usa los primeros 120 caracteres de la descripción recortados y la descripción se conserva literalmente. La fecha se convierte a medianoche UTC y ambas fechas comienzan iguales.

El ID de cada incidencia importada es un UUID5 estable derivado de `incident_id`, que se comprueba antes del insert; el identificador CSV no se almacena en el modelo. Sin identificador se usa `title + created_at`, como indica el contexto. Un identificador presente pero inválido se descarta. El analizador original sigue exigiendo `incident_id`. El seed no sobrescribe incidencias existentes, aunque se haya corregido el CSV.

Validado en base temporal: primera ejecución, **95 insertadas y 5 descartadas**; segunda, **0 insertadas y 95 omitidas**. Totales transformados: estados `open=29`, `resolved=52`, `discarded=14`; categorías `lost_parcel=14`, `carrier_issue=45`, `delivery_failure=19`, `returns_issue=17`. Un CSV con encabezados inválidos termina con código 2 sin insertar registros; las filas inválidas se reportan pero no impiden importar las válidas.

## Exportación nocturna de telemetría

`nightly_export.py` se ejecuta como proceso independiente (no dentro de FastAPI),
exporta un snapshot diario inmutable en `mock_data/raw/telemetry_YYYY-MM-DD.csv`,
y luego invoca como subproceso el pipeline semanal existente de
`mock_data/pipelines/pipeline.py`. Usa `TARGET_DATE=YYYY-MM-DD` para reejecutar
un día; por defecto elige ayer en UTC. `REPORTING_READ_DATABASE_URL` y
`REPORTING_WRITE_DATABASE_URL` se usan si están definidas y, en su defecto, el
script usa `DATABASE_URL`. Las migraciones de telemetría, reporting y
`job_runs` deben estar aplicadas.

Ejecución manual desde la raíz (con el entorno Python del proyecto instalado):

```bash
python scripts/nightly_export.py
TARGET_DATE=2026-11-17 python scripts/nightly_export.py
```

El módulo `services/job_runner.py` mantiene `public.job_runs`. Una fila con
`status='processing'` actúa como lock: la nueva invocación se registra como
cancelada (`failed`) y termina sin trabajo. Los días completados son no-op
idempotentes, con la omisión registrada como `completed`. Un día fallido se
puede reintentar. El fichero CSV no es la fuente del pipeline: el pipeline
vuelve a leer la base de datos.

El lock de fila persiste hasta que el runner actualiza el estado terminal.
Si el proceso o el host muere sin ejecutar el manejador de errores, la fila
puede quedar en `processing` indefinidamente: no hay lease/timeout ni
recuperación automática y se requiere intervención operativa. Los errores
capturables del runner sí se marcan `failed`.

El disparador elegido es **cron del sistema operativo**, porque el job es un
proceso batch independiente y no debe depender del ciclo de vida ni del hilo de
FastAPI. Tras configurar las variables de entorno de conexión en el entorno del
usuario de cron, instalarlo por ejemplo a las 02:15 hora local con:

La entrada de ejemplo está disponible en `scripts/nightly_export.cron.example`.
El ejemplo no está instalado/configurado en este repositorio: hay que
incorporarlo al crontab del host, definir allí las variables DB y reemplazar
las rutas de ejemplo.

```cron
15 2 * * * cd /ruta/al/monorepo && /ruta/al/monorepo/services/api/.venv/bin/python scripts/nightly_export.py >> /ruta/al/monorepo/nightly-export.log 2>&1
```

La fecha objetivo se resuelve en UTC independientemente de la zona horaria de
cron. La exclusión entre instancias se basa exclusivamente en el estado
`processing` de `job_runs`, no en advisory locks ni en una columna adicional.
