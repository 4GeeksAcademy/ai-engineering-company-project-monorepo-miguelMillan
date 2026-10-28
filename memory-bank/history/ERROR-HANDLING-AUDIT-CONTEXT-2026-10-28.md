# ERROR-HANDLING-AUDIT — contexto y registro de remediación — 2026-10-28

## Motivo y alcance

Feedback de la entrega anterior: la skill describía correctamente la auditoría, pero el PR solo contenía Markdown. Para completar la entrega se pidió incluir el informe y correcciones demostrables en frontend, backend y scripts. El trabajo se limitó a resiliencia y comunicación de errores, sin añadir funcionalidades de negocio.

Se revisaron las operaciones asíncronas del backoffice React/Vite, los wrappers HTTP, FastAPI y el gateway Node que ejecuta el worker Python, además de los scripts de análisis y seed. Se comprobó el estado de carga/error, acciones de recuperación, limpieza en `finally`, sanitización de mensajes, códigos HTTP y códigos de salida de CLI.

Este histórico se creó inicialmente como copia exacta y, a solicitud posterior, se amplió con el registro detallado de la tarea; por tanto, su contenido actual es el registro de auditoría/remediación y no una copia byte a byte del contexto raíz.

## Cambios realizados

### Frontend

- `uis/backoffice/src/utils/auth.ts`: `apiFetch` traduce fallos de conexión a un mensaje legible con una indicación para reintentar. Si la petición fue abortada, propaga la cancelación en lugar de presentarla como error de red.
- `uis/backoffice/src/app/account/AccountProfile.tsx`: separa el error de carga del error de guardado. Si `/auth/me` falla, no muestra el formulario con valores vacíos, evita sobrescribir datos no cargados y ofrece reintentar la lectura.
- `uis/backoffice/src/components/AuthPage.tsx`: la comprobación de sesión del cambio de contraseña limpia `checking` en `finally`; mantiene el formulario bloqueado hasta verificar la sesión y ofrece navegación al login en caso de fallo. Los errores de conexión se traducen a texto comprensible.
- `uis/backoffice/src/components/IncidentAnalysis.tsx`: la descarga CSV muestra estado de carga, se limpia en `finally`, confirma éxito y deja disponible el reintento tras un error.

### Backend y worker

- `services/api/src/server.js`: el worker Python ya no expone `stderr` al cliente. Solo el error esperado de validación se transforma en HTTP 422 con JSON; respuestas inválidas y fallos inesperados se convierten en HTTP 500 genérico. El log del gateway no imprime el objeto de excepción crudo.
- `services/api/src/server.js`: maneja `EPIPE` si el worker termina antes de consumir su entrada. El cierre del proceso completa la clasificación del error y evita que una escritura a `stdin` termine el proceso del gateway.
- `scripts/incidents_analysis.py`: los errores esperados del modo worker se serializan como JSON en `stdout`, conservando el código de salida 2 para que el gateway distinga validación de fallo interno.
- La frontera global de FastAPI ya devuelve errores 500 estructurados y genéricos, sin stack trace ni detalles internos; no se modificó porque cumplía este criterio.

### Scripts

- `scripts/analyze.py`: captura `OSError` al escribir `results.csv`, comunica por `stderr` que se comprueben permisos y espacio disponible, y devuelve código 2. El flujo normal devuelve 0 y los errores previos de lectura/validación siguen devolviendo 2.
- `scripts/seed_incidents.py`: revisión confirmó que ya captura errores de formato, lectura y persistencia, informa por `stderr` y devuelve código distinto de cero; no necesitó cambios.

## Hallazgos documentados

El informe detallado está en `docs/ERROR_HANDLING_AUDIT.md`. Se registraron estos problemas demostrables:

- **ALTO — TRY/CATCH AUSENTE:** salida temprana del worker podía emitir `EPIPE` sin manejador y derribar el gateway. Corregido y probado con worker sustituido por `/bin/false`.
- **MEDIO — ESTADOS DE CARGA/ERROR AUSENTES EN UI:** el perfil podía mostrar campos vacíos tras fallar su carga; la comprobación de sesión podía dejar el formulario bloqueado y sin salida adecuada. Corregido con error visible, reintento o enlace de login y limpieza de estado.
- **MEDIO — EXPOSICIÓN DE ERRORES EN CRUDO:** el gateway reenviaba `stderr` del worker y clasificaba todo fallo como 422. Corregido con contrato JSON, 422 solo para validaciones reconocidas y 500 genérico para fallos internos.
- **BAJO — ESTADOS DE CARGA/ERROR AUSENTES EN UI:** la descarga CSV no comunicaba carga/éxito ni distinguía una descarga fallida. Corregido con estado dedicado, confirmación y reintento.
- **BAJO — TRY/CATCH AUSENTE:** el fallo de escritura de `results.csv` escapaba del manejo operativo. Corregido con mensaje seguro y salida 2.

## Verificaciones ejecutadas

- `npm --prefix uis/backoffice run build`: correcto (TypeScript y Vite).
- `node --check services/api/src/server.js`: correcto.
- Parseo AST con Python de `scripts/analyze.py` y `scripts/incidents_analysis.py`: correcto.
- Petición HTTP real al gateway con CSV inválido: respuesta 422 JSON, sin traceback.
- Petición HTTP real con el worker sustituido por `/bin/false`: respuesta 500 JSON genérica; gateway continuó activo y el cuerpo no contenía `EPIPE` ni traceback.
- Prueba aislada del CLI con fallo de escritura simulado: `main()` devolvió 2, escribió un mensaje operativo en `stderr` y no produjo traceback.
- `get_errors` no encontró errores en los archivos TypeScript y JavaScript revisados.
- No se generaron pruebas nuevas ni se ejecutó la suite completa. Tampoco se verificó la UI en navegador.

## Límites y estado

- No se modificaron reglas de negocio, ni la skill original de auditoría de solo lectura, ni archivos `.env` o secretos.
- No se ejecutó la suite completa de frontend/backend ni una comprobación visual en navegador.
- Los históricos de backend y seed mencionados en registros previos no existían en este checkout; se usaron el briefing TrackFlow, `memory-bank/progress.md` y el historial de incidencias disponible.
- `CONTEXT.md` sigue intacto respecto a esta tarea. El progreso de implementación y validación se mantiene en `memory-bank/progress.md`.
