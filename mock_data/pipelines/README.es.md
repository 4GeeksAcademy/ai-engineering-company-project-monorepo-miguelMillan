# Carpeta `mock_data/pipelines`

Esta carpeta agrupa **todos los pipelines de datos del monorepo** relacionados con la compañía: procesos de ingesta, ETL/ELT, limpieza, transformación y carga hacia sistemas analíticos o de producción.

Cada subcarpeta o archivo dentro de `data/pipelines/` debe representar **un pipeline o conjunto de jobs** (por ejemplo `sales-etl`, `telemetry-stream`, `customer-segmentation`) e incluir la configuración necesaria (scripts, orquestación, conectores, esquemas, etc.).

- **Propósito principal**: unificar en un único lugar la lógica de movimiento y transformación de datos que soporta las aplicaciones y analíticas de la compañía.
- **Recomendación**: documenta aquí los pipelines que vayas añadiendo, describiendo su objetivo, orígenes/destinos de datos, dependencias y cómo ejecutarlos en desarrollo, pruebas y producción.

## Pipeline semanal de TrackFlow

[`pipeline.py`](pipeline.py) conserva el flow principal y su entrypoint CLI. En este
checkout `mock_data/` es la carpeta renombrada de `data/`; no hay dos pipelines.
El destino sigue siendo `reporting.weekly_warehouse_client_performance`.

El coordinador del flow principal invoca estos subflows en secuencia, pasando los
resultados como argumentos, sin compartir datos mediante variables globales:

| Subflow (`@flow`) | Inputs | Output |
| --- | --- | --- |
| `extract_weekly_performance` | Semana ISO y huella de la conexión de lectura para el caché | Lista de eventos de `telemetry_events`. |
| `transform_weekly_performance` | Lista de eventos y semana ISO | `TransformResult`: filas KPI y número de eventos únicos transformados. |
| `load_weekly_performance_subflow` | Filas KPI, semana ISO y huella de la conexión de escritura | Número de filas cargadas. |
| `export_weekly_evaluation` (opcional) | Semana ISO y filas KPI | Ruta del snapshot JSON de evaluación. |

La exportación se invoca con `return_state=True`: si falla, se registra una
advertencia y se devuelve su estado, sin convertir la carga ya confirmada en un
fallo. Los locks, checkpoints, reintentos de SQL y staging/upsert transaccional
permanecen en su lugar. Las credenciales siguen siendo configuración de entorno,
no parámetros de los subflows ni parte de los resultados.

Las tasks puras están en
[`../process/weekly_performance.py`](../process/weekly_performance.py):
`prepare_weekly_events`, `calculate_inbound_units`, `calculate_event_counts` y
`calculate_discrepancy_rate`. Suman unidades de entrada, cuentan eventos de
salida/quiebre/discrepancia y calculan la razón discrepancias/salidas (cero sin
salidas), siempre por almacén/cliente y semana UTC. La razón no es un porcentaje.

### Instalar, ejecutar y probar desde la raíz

Requiere Python >= 3.10 y `uv`; Prefect está declarado como `>=3,<4` en el proyecto
Python existente de la API:

```bash
uv sync --project services/api
source services/api/.venv/bin/activate
python -m pytest tests/pipelines/test_pipeline.py
python mock_data/pipelines/pipeline.py --help
python mock_data/pipelines/pipeline.py --week-start 2026-11-09 --trigger manual
```

La corrida real requiere `REPORTING_READ_DATABASE_URL` (solo lectura de
`telemetry_events`) y `REPORTING_WRITE_DATABASE_URL` (destino reporting con sus
migraciones aplicadas). No se requieren credenciales para los tests.

Los tests llaman `.fn` para ejecutar las tasks de forma aislada sin servidor
Prefect, bases de datos ni APIs. Cubren valores calculados a mano, separación de
clientes/almacenes, cantidades inválidas, ausencia de cliente, duplicados, límites
semanales UTC, denominador cero y entradas vacías.
