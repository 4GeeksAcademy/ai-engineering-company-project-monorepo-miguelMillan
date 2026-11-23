# Evidencia: notificaciones SSE y recuperación tras una caída

Ejecución real del 2026-11-23 de `scripts/rfp_live_smoke.cjs`: Supabase, API, worker de Celery y dos pestañas de Chromium (dashboard y `/rfps`). El API se tumba con `kill -9`. El LLM estaba desactivado a propósito (la clasificación usó las reglas) y la Parte 2 también (`RFP_AUTO_GENERATE=0`): aquí se prueba el tiempo real.

| Paso | Resultado |
|---|---|
| Otra persona sube un pitch y una RFP | Solo la RFP avisa, en las dos pestañas, sin recargar ni volver a pedir la lista |
| API caído | La UI muestra los reintentos con espera creciente (intento 5: 10 s; intento 6: 19 s) |
| Se clasifica otra RFP con el API caído | Nadie la recibe todavía |
| API levantado | Llega sola por `Last-Event-ID`: una fila y un aviso por pestaña, sin pedir la lista |
| Recarga de página | Lo descartado no reaparece; lo no leído se conserva |

- `verificacion.json`: cada comprobación, con su instante.
- `01-dashboard-nueva-rfp.png`, `02-rfps-nueva-fila.png`, `03-reconectando.png`, `04-recuperado-sin-duplicar.png`.

La primera ejecución destapó un fallo, ya corregido y comprobado en esta: tras recuperarse, `/rfps` seguía mostrando el error de un refresco hecho durante la caída.

## Red cortada (no solo el API caído)

`scripts/rfp_live_network.cjs`, con el build de producción, un proxy TCP entre el navegador y el backoffice, y una SQLite desechable. Sin ningún modelo.

| | Corte limpio | «Agujero negro» (no pasan bytes y nadie cierra) |
|---|---|---|
| Detección | Inmediata | 41 s (límite: 45 s sin bytes, ni keep-alive) |
| Intentos | Separados 1,6 s, 2,5 s y 7,5 s | Reconecta al volver la red |
| Creados sin red | 2 tickets | 2 tickets |
| Recuperación | `Last-Event-ID: 6`, lista pedida 0 veces, sin recargar | `Last-Event-ID: 8`, lista pedida 0 veces, sin recargar |
| Filas / avisos por ticket | 1/1 en los 3 | 1/1 en los 5 |

Tras volver la red, la reconexión espera a que termine el intervalo de backoff en curso (19 s en el corte limpio).

Con `vite dev` la página sí se recarga al volver la red, por el cliente HMR de Vite. No ocurre con el build.
