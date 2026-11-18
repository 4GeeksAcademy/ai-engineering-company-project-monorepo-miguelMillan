Necesitamos un script que se ejecute cada noche de forma automática, sin intervención manual. El script debe exportar los datos de telemetría del día anterior a CSV (si aún no se ha hecho), lanzar el pipeline de datos, y dejar constancia en base de datos de qué pasó y cuándo.

Criterios de aceptación:

El script es un proceso completamente independiente de la API — no puede bloquear ningún endpoint ni ejecutarse en el mismo hilo de FastAPI.
Si el script ya está corriendo cuando el siguiente ciclo lo dispara, la segunda instancia debe abortar silenciosamente. No dos ejecuciones en paralelo.
Si el script falla, el estado de ese registro en base de datos debe quedar en failed, no en processing. Ningún registro puede quedarse zombi.
El script debe ser idempotente: si se ejecuta dos veces sobre el mismo día, el resultado debe ser el mismo que si se hubiera ejecutado una sola vez.
Cada ejecución queda registrada con timestamp, estado (pending → processing → completed | failed) y, en caso de error, el mensaje de la excepción.
Pon el script en scripts/ y la lógica de estado en services/. El disparador queda en crontab o en el scheduler del framework — la decisión es tuya, justifícala en el PR.

Revisa tu monorepo: identifica las tablas de telemetría existentes y las convenciones de nomenclatura que ya has usado para rutas y campos.
Crea la tabla de control de ejecuciones en base de datos (puedes añadirla al schema existente o crear una migración nueva).
Implementa el script en scripts/nightly_export.py y el servicio de control de estado en services/.
Configura el disparador en crontab o en el scheduler de tu framework y documenta la expresión cron en el PR.