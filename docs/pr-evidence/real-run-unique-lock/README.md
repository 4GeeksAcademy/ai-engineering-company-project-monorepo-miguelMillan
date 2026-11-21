# Lock del job nocturno: índice único parcial, sin LOCK TABLE (revisión del PR #33)

**Revisión:** «`create_job` hace `LOCK TABLE public.job_runs IN SHARE ROW EXCLUSIVE MODE` y el índice `ix_job_runs_processing` no es único, así que `processing` no es el lock por sí solo; la segunda instancia se cancela por la tabla bloqueada, no por el estado».

## Cambio

| Antes | Ahora |
|---|---|
| `LOCK TABLE public.job_runs IN SHARE ROW EXCLUSIVE MODE`, luego `SELECT` del estado y luego `INSERT`/`UPDATE` | **Sin LOCK TABLE** |
| `ix_job_runs_processing` sobre `(job_name, status) WHERE status='processing'`, **no único** | `ux_job_runs_one_processing_per_job`, **ÚNICO** sobre `(job_name) WHERE status = 'processing'` (migración `supabase/migrations/20261121100000_job_runs_unique_processing_lock.sql`) |
| `pending` y `processing` en la misma transacción bloqueada: `pending` nunca era observable | La fila se inserta en `pending` y se confirma. Después, **un único `UPDATE` atómico** hace `pending → processing` si el día no está completado. Si ya hay otra fila `processing` del job, Postgres rechaza ese `UPDATE` con `UniqueViolation` en el índice y la ejecución se cancela |
| La exclusión venía del bloqueo de la tabla | La exclusión la garantiza **la propia fila `processing`**, gracias al índice único |

Con el lock tomado se vuelve a comprobar si el día ya está `completed`, porque en READ COMMITTED la nueva sentencia ve una instantánea nueva. Así se cubre el caso en que otra ejecución terminó justo entre medias: el claim se deshace y la ejecución queda registrada como duplicado.

Código: `services/job_runner.py` (`create_job`). Tests: `tests/test_job_runner.py`, contra SQLite con el **mismo índice único parcial**, así que la violación de unicidad es real. Incluyen que `create_job` no contiene `LOCK TABLE`.

## Ejecución real contra Supabase (2026-11-21)

Se usó la misma base que en el PR #33 (`REPORTING_READ_DATABASE_URL` = `REPORTING_WRITE_DATABASE_URL` = `DATABASE_URL`). Los logs no contienen la URL, el host ni la contraseña (revisado).

| # | Qué | Resultado | Archivo |
|---|---|---|---|
| 1 | Migración aplicada; índices antes y después | Desaparece `ix_job_runs_processing` (no único) y aparece `ux_job_runs_one_processing_per_job` (**UNIQUE** … `WHERE status = 'processing'`) | [01-migration-and-index-proof.log](01-migration-and-index-proof.log) |
| 1b | El índice **por sí solo**: dos `INSERT` de filas `processing` del mismo job en una transacción revertida | La segunda falla con **`UniqueViolation constraint=ux_job_runs_one_processing_per_job`**; la prueba no deja filas | Ídem |
| 2 | Dos instancias concurrentes, 2026-11-12: A y, 100 ms después, B | A **completed**. B se cancela a los 33 ms con `lock=unique_index constraint=ux_job_runs_one_processing_per_job` | [A](02a-2026-11-12-instance-A.log), [B](02b-2026-11-12-instance-B.log) |
| 2c | Sondeo de esa corrida | Falló al empezar: el pooler de Supabase (pgbouncer) no admite las sentencias preparadas de psycopg. Se conserva tal cual; corregido (`prepare_threshold=None`) y repetido en la prueba 3 | [02c](02c-2026-11-12-poll-crashed-pgbouncer.log) |
| 3 | Repetición, 2026-11-11, con sondeo de `job_runs` y `pg_locks` cada 0,2 s | A **completed**; B se cancela por el índice único. El sondeo muestra a **B en `pending`** mientras **A está en `processing`**, luego B en `failed`, y **ningún bloqueo de tabla**: solo `RowExclusiveLock`, el de cualquier `INSERT`/`UPDATE` | [A](03a-2026-11-11-instance-A.log), [B](03b-2026-11-11-instance-B.log), [sondeo](03c-2026-11-11-job_runs-and-pg_locks-poll.log) |
| 4 | Filas finales de `public.job_runs` | Ver más abajo | [05-final-job_runs.txt](05-final-job_runs.txt) |
| 5 | Re-ejecución de un día ya completado (2026-11-12) | No-op idempotente `reason=duplicate`, exit 0 | [06-rerun-completed-day.log](06-rerun-completed-day.log) |

Extracto del sondeo de la prueba 3:

```
17:13:40.855 job_runs: []   locks sobre public.job_runs: ['RowExclusiveLock']
17:13:41.123 job_runs: ['6d4e6390 processing started=True', '5d5310cd pending started=False']   locks: ninguno
17:13:41.394 job_runs: ['6d4e6390 processing started=True', '5d5310cd failed … Cancelled: another nightly_export run is processing.']
17:13:52.928 job_runs: ['6d4e6390 completed …', '5d5310cd failed … Cancelled: another nightly_export run is processing.']
```

```
id         target_date status     started_at                 finished_at                error_message
1a25eb68   2026-11-12  completed  2026-11-21 17:12:58.363    2026-11-21 17:13:20.647
c7e36520   2026-11-12  failed     -                          2026-11-21 17:12:58.579    Cancelled: another nightly_export run is processing.
6d4e6390   2026-11-11  completed  2026-11-21 17:13:40.914    2026-11-21 17:13:52.748
5d5310cd   2026-11-11  failed     -                          2026-11-21 17:13:41.122    Cancelled: another nightly_export run is processing.
```

Los CSV de los días 2026-11-11 y 2026-11-12 solo tenían la cabecera (esos días no hay eventos) y se eliminaron, como en la evidencia original.
