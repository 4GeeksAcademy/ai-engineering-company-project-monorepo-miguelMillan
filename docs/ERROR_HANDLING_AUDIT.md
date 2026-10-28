# Informe de auditoría de gestión de errores

**Fecha:** 2026-10-28  
**Alcance:** operaciones asíncronas de `uis/backoffice/src`, límites HTTP de FastAPI y gateway Node en `services/api`, y gestión de I/O en `scripts/analyze.py` y `scripts/seed_incidents.py`.

## Hallazgos

### [ALTO] [TRY/CATCH AUSENTE] — `services/api/src/server.js:52`
- **Problema:** si el worker termina antes de consumir la entrada, escribir en `stdin` genera `EPIPE` sin manejador y puede terminar el proceso del gateway, afectando todas sus rutas.
- **Corrección aplicada:** se maneja `EPIPE` de forma acotada y el evento `close` mantiene la clasificación del worker como fallo interno 500.
- **Verificación:** worker sustituido por `/bin/false`; el gateway siguió activo y respondió 500 JSON genérico sin traceback.

### [MEDIO] [ESTADOS DE CARGA/ERROR AUSENTES EN LA UI] — `uis/backoffice/src/app/account/AccountProfile.tsx:50`
- **Problema:** si falla la lectura inicial de `/auth/me`, la pantalla podía mostrar el formulario con valores vacíos y permitir guardar, sobrescribiendo datos de perfil que no llegaron a cargarse.
- **Corrección aplicada:** el error de carga ahora sustituye al formulario, ofrece reintento y solo habilita la edición tras una carga correcta. Los errores de red se traducen en el cliente a un mensaje legible con una acción (`uis/backoffice/src/utils/auth.ts:35`).
- **Verificación:** `npm --prefix uis/backoffice run build` correcto.

### [MEDIO] [ESTADOS DE CARGA/ERROR AUSENTES EN LA UI] — `uis/backoffice/src/components/AuthPage.tsx:50`
- **Problema:** si fallaba la comprobación de sesión para cambiar la contraseña, `checking` no se liberaba; además, un rechazo de red podía mostrar el mensaje técnico del navegador y no ofrecía una salida clara.
- **Corrección aplicada:** `finally` limpia el estado, el formulario permanece bloqueado si no se verificó la sesión y aparece un enlace para iniciar sesión. El wrapper HTTP traduce los fallos de conexión.
- **Verificación:** compilación TypeScript y build de backoffice correctos.

### [MEDIO] [EXPOSICIÓN DE ERRORES EN CRUDO] — `services/api/src/server.js:50`
- **Problema:** el gateway reenviaba `stderr` del worker Python al cliente y etiquetaba cualquier salida no exitosa como 422; un fallo inesperado podía revelar tracebacks/rutas internas y recibir un código HTTP incorrecto.
- **Corrección aplicada:** los errores esperados del worker se serializan como JSON y solo el código conocido de validación se convierte a 422. Salida inválida o fallos inesperados reciben un 500 genérico; `stderr` no se envía al cliente y el log del gateway no imprime la excepción.
- **Verificación:** petición HTTP real con CSV inválido devolvió 422 JSON sin traceback; worker fallido respondió 500 genérico. El límite global de FastAPI también devuelve un 500 genérico estructurado.

### [BAJO] [ESTADOS DE CARGA/ERROR AUSENTES EN LA UI] — `uis/backoffice/src/components/IncidentAnalysis.tsx:114`
- **Problema:** la descarga de resultados no tenía estado propio de carga o éxito, por lo que un rechazo no distinguía la petición pendiente ni confirmaba la descarga.
- **Corrección aplicada:** estado `downloading`, limpieza en `finally`, aviso de éxito y botón disponible para reintentar.
- **Verificación:** build de backoffice correcto.

### [BAJO] [TRY/CATCH AUSENTE] — `scripts/analyze.py:49`
- **Problema:** un error de I/O al escribir `results.csv` escapaba del flujo controlado y producía traceback, aunque el proceso terminara con fallo.
- **Corrección aplicada:** se captura `OSError`, se informa por `stderr` sin detalles internos y `main()` devuelve código 2.
- **Verificación:** fallo de escritura simulado; retorno 2, mensaje operativo y sin traceback.

## Cobertura y límites

- Revisados los estados carga/éxito/error, limpieza y recuperación de las operaciones asíncronas del backoffice; rutas y wrappers HTTP de FastAPI/gateway; y lectura, parseo y escritura de los scripts de análisis y seed.
- Las rutas FastAPI ya producen respuestas JSON de error sin traceback en la frontera global; los scripts de seed ya distinguen errores de I/O/formato y devuelven códigos distintos de cero. No se encontraron hallazgos adicionales demostrables en esos puntos.
- Validaciones ejecutadas: build de backoffice, petición HTTP de integración al gateway y simulación de fallo de escritura del CLI. No se ejecutó la suite completa ni una prueba visual de navegador.
- `CONTEXT.md` estaba vacío en el checkout; se usaron el briefing TrackFlow y las decisiones técnicas registradas en `memory-bank/progress.md`. No se leyeron archivos `.env` ni dependencias/artefactos generados.
