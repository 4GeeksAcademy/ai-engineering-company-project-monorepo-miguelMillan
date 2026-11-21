# Ejecución real del job nocturno contra Supabase (2026-11-19)

> **Actualización (2026-11-21):** el lock ya no usa `LOCK TABLE`. Ahora lo garantiza un índice único parcial sobre `processing`, con la transición `pending → processing` atómica, y `pending` sí es observable. Ver [real-run-unique-lock](../real-run-unique-lock/README.md). Lo que sigue describe la ejecución original del PR #33.

Esta carpeta reúne la evidencia de los puntos pendientes de la revisión del PR #30. Todo se ejecutó contra la base de datos Supabase de `DATABASE_URL`. Los logs se revisaron y no contienen la URL, el host ni la contraseña.

Entorno: `uv sync` en la raíz con los grupos `nightly` y `dev`. Se usó `REPORTING_READ_DATABASE_URL` = `REPORTING_WRITE_DATABASE_URL` = `DATABASE_URL`, porque en este entorno hay una sola base de datos.

## 1. Migraciones aplicadas

No existía un esquema de control de migraciones (`supabase_migrations`), así que se aplicaron a mano en orden, cada una en su propia transacción:

1. `20261116170800_create_reporting_pipeline_tables.sql`: esquema `reporting`, `pipeline_runs` y `weekly_warehouse_client_performance`.
2. `20261116180000_add_prefect_run_id_and_skipped_status.sql`
3. `20261118100000_create_job_runs.sql`: `public.job_runs`.

Las dos primeras eran necesarias. Sin `reporting.pipeline_runs` el pipeline no puede terminar y no habría filas de `pipeline_runs` que mostrar.

## 2. Ejecuciones

| # | Comando | Resultado | Log |
|---|---|---|---|
| 1 | `TARGET_DATE=2026-11-15 uv run python scripts/nightly_export.py` sin `REPORTING_*` | **failed** (exit 1). Exportó el CSV, pero el pipeline exige `REPORTING_WRITE_DATABASE_URL` | [01-failed-missing-reporting-url.log](01-failed-missing-reporting-url.log) |
| 2 | El mismo, con `REPORTING_*` definidas | **completed** (exit 0). Pipeline Prefect completo y reutilización del CSV existente (`snapshot_exists_no_overwrite`) | [02-completed.log](02-completed.log) |
| 3 | Otra vez el mismo día | **no-op** `skipped reason=duplicate` (exit 0) | [03-rerun-same-day.log](03-rerun-same-day.log) |
| 4a | Dos instancias concurrentes, 2026-11-15 | Ambas **no-op** por día ya completado | [A](04a-concurrent-2026-11-15-A.log), [B](04a-concurrent-2026-11-15-B.log) |
| 4b | Dos instancias concurrentes, 2026-11-14 (día nuevo) | A **completed**. B, 100 ms después, se **cancela** con `reason=processing` y deja una fila `failed` | [A](04b-concurrent-2026-11-14-A.log), [B](04b-concurrent-2026-11-14-B.log) |
| 5 | Sondeo de `job_runs` cada 0,3 s durante una ejecución (2026-11-13) | Fila real en **processing** (sin `finished_at`) y su transición a completed | [06-job_runs-poll-processing.log](06-job_runs-poll-processing.log), [06-processing-capture-2026-11-13.log](06-processing-capture-2026-11-13.log) |
| 6 | Wrapper de cron en un entorno vacío (`env -i`, igual que cron) | **no-op** (exit 0). Sin archivo de entorno da `missing_env_file` (exit 1) | [08-cron-wrapper-clean-env.log](08-cron-wrapper-clean-env.log) |

[05-job_runs-poll-during-concurrency.log](05-job_runs-poll-during-concurrency.log) es un primer sondeo que agotó sus 90 s antes de la prueba 4b. Se conserva tal cual, y por eso se repitió la captura en la prueba 5.

## 3. Filas reales de `public.job_runs` y `reporting.pipeline_runs`

Fuente completa: [07-final-tables.txt](07-final-tables.txt).

```
id         job_name        target_date status     started_at                 finished_at                error_message
95a91dbd   nightly_export  2026-11-15  failed     2026-11-19 17:59:07.917Z   2026-11-19 17:59:27.574Z   CalledProcessError: … pipeline.py --week-start 2026-11-09 … exit status 1
e5b79487   nightly_export  2026-11-15  completed  2026-11-19 17:59:43.094Z   2026-11-19 17:59:56.662Z
98e43928   nightly_export  2026-11-15  completed  -                          2026-11-19 18:00:14.968Z   (re-ejecución: no-op)
2acd7679   nightly_export  2026-11-15  completed  -                          2026-11-19 18:00:31.585Z   (concurrente A: no-op)
28a0be16   nightly_export  2026-11-15  completed  -                          2026-11-19 18:00:31.716Z   (concurrente B: no-op)
dca6f6d0   nightly_export  2026-11-14  completed  2026-11-19 18:01:59.958Z   2026-11-19 18:02:15.203Z
ff3784bf   nightly_export  2026-11-14  failed     -                          2026-11-19 18:02:00.059Z   Cancelled: another nightly_export run is processing.
9500f3f0   nightly_export  2026-11-13  completed  2026-11-19 18:02:27.762Z   2026-11-19 18:02:42.694Z   (visto en processing a las 18:02:28)
ec2ffdbc   nightly_export  2026-11-15  completed  -                          2026-11-19 18:04:13.668Z   (wrapper cron: no-op)
```

```
run_id     pipeline_name                        week_start  status     phase      trigger    read  transf written
6df983f0   weekly_warehouse_client_performance  2026-11-09  succeeded  completed  scheduled  0     0      0
e359813a   weekly_warehouse_client_performance  2026-11-09  succeeded  completed  scheduled  0     0      0
539cbc78   weekly_warehouse_client_performance  2026-11-09  succeeded  completed  scheduled  0     0      0
```

Las tres filas de `pipeline_runs` corresponden a las tres ejecuciones del job que llegaron a lanzar el pipeline: e5b79487, dca6f6d0 y 9500f3f0. Todas caen en la semana 2026-11-09.

**Sobre los estados:**

- **`pending`** existe en el código, pero no puede observarse desde fuera. `create_job` inserta la fila en `pending` y la pasa a `processing` dentro de la misma transacción con `LOCK TABLE`, así que ninguna otra sesión llega a verla en `pending`. No se ha fabricado ninguna fila para mostrarlo.
- **`processing`** se capturó en vivo en la prueba 5.
- **`completed`** y **`failed`** aparecen en la tabla final. Hay `failed` de dos tipos: un error real del pipeline y una cancelación por concurrencia.

**Sobre `records_read = 0`:** el pipeline solo procesa eventos de almacén (`inbound_order_created`, `outbound_order_created`, `stock_threshold_triggered` e `inventory_discrepancy_detected`). En Supabase la semana del 2026-11-09 solo tiene telemetría de frontend y API: 39 eventos de tipo `api_request_completed`, `backoffice_section_viewed` y `frontend_web_vital_recorded`. El 0 es el resultado correcto con los datos reales, no un fallo. La evaluación generada está en `mock_data/eval/weekly-warehouse-client-performance-2026-11-09.json` (`entries: []`).

## 4. CSV completo

El archivo es `mock_data/raw/telemetry_2026-11-15.csv`, con 39 eventos más la cabecera. Se versiona completo. El `user_id` ya viene seudonimizado (`u_` + hash SHA-256). Estas son las primeras filas:

```csv
event_id,timestamp,session_id,user_id,event_type,schema_version,request_id,tags
0cf247a5-4d70-4baa-baf0-63492f023a1a,2026-11-15T14:47:38.608000+00:00,10f99a9c-88ac-4bd1-bd70-7c9d71e25e41,u_6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b,backoffice_section_viewed,1.0.0,3960e8be-fe60-42d8-93b4-b92b3f944e2c,"{""section"": ""overview""}"
18fb7298-4c69-4595-a301-c2334a7bfa03,2026-11-15T14:47:38.610000+00:00,10f99a9c-88ac-4bd1-bd70-7c9d71e25e41,u_6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b,backoffice_section_viewed,1.0.0,769d8952-593e-479d-b838-802126d5266a,"{""section"": ""overview""}"
8d240716-3105-4dc1-8106-022f6219322f,2026-11-15T14:47:38.637000+00:00,10f99a9c-88ac-4bd1-bd70-7c9d71e25e41,u_6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b,frontend_web_vital_recorded,1.0.0,7140f6f8-aa22-4018-9e1c-20072f334cf6,"{""metric"": ""TTFB"", ""page"": ""overview"", ""value"": 143.19999998807907}"
df97e061-e44f-444a-bb85-66539dab707e,2026-11-15T14:47:38.637000+00:00,10f99a9c-88ac-4bd1-bd70-7c9d71e25e41,u_6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b,frontend_web_vital_recorded,1.0.0,c267a262-b289-44ae-8e31-227afb6c4324,"{""metric"": ""FCP"", ""page"": ""overview"", ""value"": 1624}"
82ece7a4-d657-4f74-8a89-33376e51249e,2026-11-15T14:47:38.686000+00:00,10f99a9c-88ac-4bd1-bd70-7c9d71e25e41,u_6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b,api_request_completed,1.0.0,5b3e41b5-e849-4feb-842b-4c4896ce6754,"{""duration_ms"": 75.19999998807907, ""method"": ""GET"", ""route_group"": ""auth_me"", ""status_code"": 200}"
```

Las ejecuciones del 2026-11-13 y del 2026-11-14 generaron CSV con solo la cabecera, porque esos días no hay eventos. Se eliminaron porque no aportan datos.

## 5. Cron

- Wrapper: `scripts/nightly_export_cron.sh`. Fija el PATH, carga las URLs de base de datos desde `~/.config/trackflow/nightly-export.env` (fuera del repositorio, permisos 600), valida las tres variables y ejecuta `uv run --frozen` bajo `flock`.
- Entrada de crontab: `scripts/nightly_export.cron.example` (`15 2 * * *`).
- **Instalado en este codespace** (`sudo apt-get install cron`, servicio `cron` en marcha y archivo de entorno con `chmod 600`). El crontab instalado es [10-crontab-installed.txt](10-crontab-installed.txt).
- **Disparo real verificado.** Se añadió una entrada temporal `* * * * *` con `TARGET_DATE=2026-11-15`. Cron lanzó el job a las 18:08:02 UTC y dio un no-op `duplicate` sin errores ([09-cron-triggered.log](09-cron-triggered.log)). Esa ejecución añadió una fila `completed` más en `job_runs`, posterior a la captura de `07-final-tables.txt`. Después se retiró la entrada temporal y solo queda la diaria `15 2 * * *`.
- Limitación: un codespace se detiene y se recrea. El servicio cron y el crontab no sobreviven a un rebuild, así que para producción hay que instalarlo en un host persistente con los mismos pasos de `scripts/README.es.md`.

## 6. Tests reproducibles y `.pyc`

- El `pyproject.toml` raíz tiene un grupo `nightly` (`sqlalchemy`, `psycopg[binary]`, `prefect` y `sqlmodel`), activo por defecto junto a `dev`. SQLAlchemy 2.1 usa `psycopg` (v3) por defecto para `postgresql://`, y antes no estaba instalado en ningún entorno: el job no podía conectarse con la URL tal cual.
- `uv sync && uv run pytest tests -q` da **27 passed**: nightly, job_runner, pipeline y modelo de ventas.
- Se quitaron del índice los 18 `.pyc` versionados (`git rm --cached`), se borraron los `__pycache__/` locales y se añadieron `__pycache__/` y `*.py[cod]` a `.gitignore`.
