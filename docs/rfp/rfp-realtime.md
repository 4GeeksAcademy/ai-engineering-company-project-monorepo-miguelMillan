# Notificación en tiempo real de tickets RFP (SSE)

**Para quién:** quien mantenga el backoffice o el API. El usuario final es el equipo comercial de Miguel Torres, que hasta ahora descubría las RFP nuevas recargando el dashboard.

## Cómo funciona

```
worker (Celery): classify → RFP válida → outbox rfp_ticket_events (PostgreSQL, un evento por ticket)
API: sondeo del outbox por proceso (1 s) ─► GET /rfp-intake/events (SSE, JWT) ─► cada pestaña del backoffice
UI: fetch + ReadableStream ─► aviso violeta en todo el backoffice + fila «Nueva» en /rfps
```

| Pieza | Dónde |
|---|---|
| Contrato del evento | `mock_data/pipelines/rfp_intake/events.py`; se registra en el nodo `classify` de `graph.py` |
| Outbox | `TicketEvent` (`models.py`), migración `supabase/migrations/20261123090000_rfp_ticket_events.sql` |
| Endpoint | `services/api/routes/rfp_events.py` |
| Cliente | `uis/backoffice/src/utils/sse.ts` (parser), `rfpStream.ts` (conexión), `rfpLive.ts` (avisos), `rfpEvents.ts` (fusión sin duplicar) |
| Vista | `app/rfps/RfpLiveNotifications.tsx` (en `App.tsx`) y `app/rfps/RfpIntake.tsx` |

## Evento

```text
id: 1
event: rfp_ticket_created
data: {"ticket_id": "efcc52e1-…", "rfp_id": "RFP-20261123-EFCC52", "client_name": "Luna Cosmetics", "client_country": "US", "services_requested": ["warehouse", "lastmile"], "status": "analyzing", "created_at": "2026-11-23T16:38:06Z"}
```

- **Cuándo:** al clasificar el documento como RFP válida (BRIEFING §2), no al subir el PDF. Un descartado no notifica; si Ventas lo acepta después, entonces sí. Los reintentos y reprocesos no repiten el evento.
- **Valores:** los del BRIEFING (`analyzing`, `warehouse`/`lastmile`/`reverse`). La base conserva los suyos (`analizando`, `last_mile`…); se traducen solo en el evento.
- **Otro evento:** `rfp_stream_reset`, con `{"reason": …}`, cuando no se puede reenviar lo perdido.

## Conexión

- **Cabeceras:** `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`. Comentario `: keep-alive` cada 15 s.
- **Seguridad:** el mismo JWT del API, solo en `Authorization` (en la URL no se acepta: quedaría en logs). El stream se cierra cuando caduca el token o se desactiva el usuario.
- **Apagado:** uvicorn espera 5 s como máximo a los streams abiertos; sin ese límite, `--reload` y los despliegues se quedarían colgados.

## Reconexión y recuperación

**Estrategia elegida: `Last-Event-ID` con replay desde el outbox.**

1. El servidor envía el cursor al abrir (`id:`), aunque no haya eventos. El cliente lo guarda **después** de entregar cada evento.
2. Si se corta (o pasan 45 s sin bytes, ni keep-alive), reintenta con backoff: 1, 2, 4… hasta 30 s, con jitter. El contador se reinicia al recibir un evento o tras 10 s estables.
3. Al volver envía `Last-Event-ID` y recibe solo lo perdido, sin pedir la lista.
4. Si el hueco supera 200 eventos o el id es desconocido, llega `rfp_stream_reset`: la UI recarga la lista y lo dice en pantalla.

**Deduplicación por `ticket_id`:** una fila y un aviso, aunque el evento se reenvíe, se recargue la página o el aviso ya se descartara. Un evento reenviado nunca retrocede el estado de una fila. Las inserciones en el outbox se serializan para que los ids se vean en orden: sin eso, un lector podría saltarse un evento.

**Sin volver a pedirlo todo:** el evento trae lo necesario para pintar la fila. El refresco de los tickets en curso pide solo esos (`GET /rfp-intake/tickets?ticket_id=…`).

| Variable | Por defecto | Qué controla |
|---|---|---|
| `RFP_SSE_KEEPALIVE_SECONDS` | 15 | Frecuencia del keep-alive |
| `RFP_SSE_POLL_SECONDS` | 1 | Latencia máxima del sondeo |
| `RFP_SSE_REPLAY_LIMIT` | 200 | Eventos que se reenvían como mucho |

## Preguntas de diseño

### Dos personas a la vez: ¿conexiones independientes o capa intermedia? ¿Y 50?

**Las dos cosas.** La conexión es independiente; la detección de eventos se comparte.

- **Independiente por conexión:** el JWT y su caducidad, el cursor (`Last-Event-ID`) y el keep-alive. Cada persona puede haberse perdido cosas distintas, y que una se desconecte no afecta a otra.
- **Compartido:** el outbox `rfp_ticket_events`, que es la fuente de verdad entre el worker y el API, y un `EventHub` por proceso. El hub sondea el outbox una vez por segundo, despierta a todos los streams y se para cuando no queda ninguno.

No hay colas en memoria por cliente: cada stream lee el outbox desde su cursor. Un cliente lento no acumula memoria ni pierde eventos, y con varios procesos del API no hacen falta sesiones pegajosas.

**Con 50 personas (medido con PostgreSQL local y un proceso de API):**

| Streams | Abrirlos todos | Del registro al aviso (mediana / máx.) | Consultas en reposo | RSS del API | Pérdidas o duplicados |
|---|---|---|---|---|---|
| 2 | 0,17 s | 0,91 s / 0,93 s | 1/s | 209 MB | 0 |
| 50 | 0,40 s | 0,87 s / 0,95 s | 1/s | 216 MB | 0 |
| 150 | 1,16 s | 0,68 s / 0,88 s | no medido | 226 MB | 0 |

Son tres eventos por caso, todos los clientes en la misma máquina. La latencia la marca el sondeo de 1 s, no el número de personas. Medirlo destapó un defecto: cada stream consultaba la base tras su keep-alive, unas 3,3 consultas/s de más con 50. Está corregido y tiene prueba.

**Qué crece y qué no:**
- Por cada evento hay una consulta corta por stream, limitada por el pool (15 conexiones). Con 50 no se nota; con cientos, el hub debería leer una vez y repartir. Contra Supabase, con su latencia, no lo he medido.
- Si el API se reinicia, los 50 reconectan. El jitter del backoff evita que lo hagan a la vez.
- Con HTTP/1.1 un navegador permite seis conexiones por origen: el límite son las pestañas de una misma persona, no las personas. HTTP/2 lo resuelve.
- Con miles de conexiones pondría Redis Pub/Sub para despertar a los procesos, y dejaría el outbox para el replay.

### Recuperación de lo perdido y cómo se evitan duplicados

**`Last-Event-ID` con replay corto desde el outbox** (hasta 200 eventos). Un refetch pediría 50 tickets para recuperar uno, y no diría cuáles son nuevos.

1. El servidor envía el cursor al abrir, aunque no haya eventos: un corte antes del primero tampoco pierde nada.
2. El cliente guarda el cursor **después** de entregar el evento. Si se corta en medio, el servidor lo reenvía.
3. Al reconectar recibe solo lo posterior a su cursor.
4. Si el hueco es mayor o el id es desconocido llega `rfp_stream_reset`: la UI recarga la lista y lo dice en pantalla. La pérdida nunca es silenciosa.
5. Los ids se hacen visibles en orden (lock en la inserción). Sin eso, un lector podría avanzar al 6 antes de que el 5 se confirmara. Con 6 escritores a la vez se leyeron 60 de 60.

La entrega es «al menos una vez», así que **el duplicado se resuelve por `ticket_id`, en dos sitios**:
- **Al registrar:** `UNIQUE (ticket_id, event_type)`; un reintento de Celery o un reproceso no vuelve a avisar.
- **En la UI:** una fila y un aviso por ticket, también al recargar la página o tras descartar el aviso. Un evento reenviado no retrocede el estado de la fila.

### ¿Por qué SSE y no WebSockets? ¿Cuándo dejaría de serlo?

El flujo va **en una sola dirección**: el servidor avisa y la persona mira. Para eso SSE da:
- HTTP normal: el mismo JWT en `Authorization`, los mismos proxies, CORS y logs. Un WebSocket del navegador no admite esa cabecera y obliga a pasar el token por URL, subprotocolo o primer mensaje.
- `id:` y `Last-Event-ID` definidos en el protocolo. Con WebSockets, el replay habría que diseñarlo.
- Menos piezas: sin handshake propio ni ping/pong.

Se usa `fetch` + `ReadableStream` porque `EventSource` no permite cabeceras; a cambio, el parser y el backoff son nuestros.

**Reaccionar al ticket no obliga por sí solo a cambiar.** Aprobar una sección ya es un `POST` normal, con validación, permisos y códigos HTTP; SSE para avisar y `POST` para actuar es una buena combinación.

Dejaría de ser la herramienta correcta si el canal tuviera que ser **bidireccional y frecuente**:
- un chat sobre el ticket (la Parte 2);
- presencia («Ana está revisando esta sección») o «escribiendo…»;
- edición simultánea del borrador, donde importa el orden de los mensajes de ambos lados;
- muchos mensajes pequeños del cliente, donde una petición HTTP por cada uno pesa, o datos binarios.

## Límites conocidos

- Cada pestaña abre una conexión. Con HTTP/1.1 el navegador permite seis por origen.
- El outbox no se purga. Crece una fila por RFP.
- El caso opcional del BRIEFING §4 (`sla_threshold_alert`, `ticket_escalated_to_senior`) no está implementado.

## Pruebas

- `services/api/tests/test_rfp_events_sse.py` (12): endpoint en el wire con uvicorn real. Requiere el entorno de `services/api`, que el CI no ejecuta.
- `tests/pipelines/test_rfp_events.py` (4, en CI): cuándo se registra y con qué payload.
- `uis/backoffice/src/__tests__/rfp-live.test.ts` (12): parser, backoff, recuperación y deduplicación.
- `scripts/rfp_live_network.cjs`: red cortada y conexión muerta, con proxy TCP sobre el build.
- `scripts/rfp_live_smoke.cjs`: caída real del API con dos pestañas. Resultado en `docs/pr-evidence/rfp-sse/`.
