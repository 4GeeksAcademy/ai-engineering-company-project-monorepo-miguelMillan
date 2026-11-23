# Chat en tiempo real con el First-line CX agent (WebSocket)

**Para quién:** quien mantenga el chat o el agente. Lo pidió Valentina Cruz (Customer Experience): que la respuesta llegue token a token y que el cliente pueda redirigirla a mitad.

La lógica y las herramientas del agente no cambian: mismo grafo, mismos nodos y mismas guardas. Cambia el canal.

```
UI ──WS /chat/ws?session_id──► conexión ──► ChatEngine ──hilo──► KnowledgeAgent.stream (grafo LangGraph)
                                  ▲                                   │ publica
                                  └──── canal chat.<session_id> ◄─────┘   (pub/sub en memoria)
```

| Pieza | Dónde |
|---|---|
| Contrato de eventos | `services/agent/chat/events.py` |
| Pub/sub por sesión | `services/agent/chat/broker.py` |
| Turnos, interrupción, rehidratación | `services/agent/chat/engine.py` |
| `ChatSession` e historial | `services/agent/chat/store.py`, migración `20261123170000_chat_sessions.sql` |
| Streaming, aborto y guarda incremental | `services/agent/streaming.py`, `KnowledgeAgent.stream` en `graph.py` |
| WebSocket y sesiones HTTP | `services/api/routes/chat.py` |
| UI | `uis/backoffice`: `KnowledgeAssistant.tsx` (`/knowledge`), `utils/chat.ts` (estado), `utils/chatSocket.ts` (conexión) |

## Conexión

1. `POST /chat/sessions` crea la `ChatSession` (`session_id`, `agent_id: first_line_cx`, `user_id`, `client_id`, `status`, `created_at`). `client_id` sale de los despachos de la cuenta, nunca del navegador.
2. `WS /chat/ws?session_id=…`. La sesión debe existir y ser de quien se autentica.
3. **JWT** (el mismo del API): `?token=` o un primer frame `{"event": "auth", "data": {"token": …}}`. La UI usa el frame, que no deja el token en URLs; si llega en la URL, se borra de los logs.
4. El primer evento es siempre `session_snapshot`.

La conexión se cierra al caducar el token o al desactivarse la cuenta. Códigos: 4401 sin autenticar, 4404 sesión inexistente o ajena, 4410 cerrada, 1009 frame demasiado grande, 1013 cliente demasiado lento.

## Eventos

Los del BRIEFING §4, con sus campos: `user_message`, `token_chunk`, `interrupt_requested`, `generation_interrupted`, `generation_completed`, `session_snapshot`. Se añaden tres de transporte: `auth`, `error` y `ping`/`pong` (el navegador no detecta por sí solo una conexión muerta). Nada en común con los eventos RFP de la Parte 1.

- `sequence` empieza en 1 en cada respuesta.
- Cada mensaje del snapshot lleva `message_id`, `role`, `text`, `status` (`streaming`, `completed`, `interrupted`, `failed`) y `created_at`; la respuesta en curso, también `sequence`.
- `interrupt_requested` con `new_input` vacío solo detiene. Un `user_message` mientras el agente responde equivale a redirigirlo.

## Streaming

`stream_mode=["custom", "values"]`. El modo `messages` no aplica: el agente llama al SDK de OpenAI, no a un ChatModel de LangChain. El nodo `generate` emite cada fragmento con `get_stream_writer`; los textos deterministas (tools, guardas) van por el mismo canal.

**La guarda de salida no se salta.** Antes validaba la respuesta completa antes de mostrarla. `StreamGuard` aplica las mismas reglas a cada fragmento y retiene solo el tramo que aún podría completar un patrón prohibido (una cifra en euros cuyo transportista no ha aparecido, media marca interna, un pedido a medio escribir). La respuesta final sigue pasando por `output_guard`; si la sustituye, llega un `session_snapshot` con la versión válida.

## Interrupción

1. `Generation.abort` marca la respuesta y publica `generation_interrupted` con el mismo lock que publica tokens: después no puede salir ninguno de esa generación.
2. Cancela la tarea y cierra la petición HTTP al modelo, también si aún no llegó el primer token. No se ejecutan más nodos.
3. El parcial queda en su fila con `status = interrupted` y no se vuelve a escribir. `new_input` es un turno nuevo, con otro `message_id`.

No se usa `interrupt()` de LangGraph: eso pausa el grafo para un humano.

Una respuesta interrumpida tampoco deja pendiente su propuesta de memoria: el usuario no la vio y el siguiente «sí» la aprobaría.

## Reconexión

Backoff de 1 a 30 s con jitter y el mismo `session_id`. Hasta el `session_snapshot` no se acepta ningún token ni se puede escribir. La foto y la suscripción se toman a la vez: la respuesta en curso sigue donde iba, sin repetir ni perder.

Si el mensaje del usuario no llegó a salir, vuelve al cuadro de texto. Si el API murió a mitad, el parcial guardado (cada 2 s) aparece como `interrupted`.

**`session_id` y `thread_id`:** la conversación se restaura desde el historial persistido. El agente es de un turno por diseño y su checkpoint usa un `thread_id` por respuesta (`run_id` = `message_id`). Reutilizar el de la sesión arrastraría los resultados de las tools de un turno al siguiente y cambiaría su lógica. `session_id` es el único id de la conversación: handshake, canal, historial, memoria y estado de cada checkpoint.

## Preguntas de diseño

### ¿Por qué WebSockets y no lo de la Parte 1? ¿Qué obliga a un canal bidireccional?

En la Parte 1 el servidor avisa y la persona mira: un sentido, y SSE basta. Aquí **la persona interviene en algo que el servidor todavía está emitiendo**. Lo que obliga al canal de vuelta es la interrupción a mitad de respuesta, no el streaming: solo para ver los tokens, SSE habría servido.

Lo que aporta que ida y vuelta sean el mismo canal:
- **La conexión identifica la conversación.** `interrupt_requested` llega por el socket ya autenticado y ligado al `session_id`. El corte se hace en memoria, sin consultar la base: eso bajó de 207 a 86 ms con el modelo real.
- **Orden único.** `generation_interrupted`, el `user_message` nuevo y sus tokens llegan por el canal en el orden en que ocurrieron. El cliente no tiene que casar la respuesta de un `POST` con un stream aparte.
- **Conversación rápida en los dos sentidos**, incluido el latido (`ping`/`pong`) que detecta una conexión muerta.

**Matiz:** SSE más `POST /interrupt` también podría hacerlo. Habría que localizar la generación desde otra petición, quizá en otro proceso, y reconciliar dos canales. Con interrupciones frecuentes, ese trabajo no compensa. Lo que se paga: sin `Authorization` en el handshake, y sin `Last-Event-ID`. Por eso hay frame de autenticación y `session_snapshot`.

### Varios clientes en la misma sesión: mismos eventos, una sola llamada al agente

La llamada depende del **turno**, no de la conexión. `ChatEngine` inicia una `Generation` por turno, en un hilo que publica en `chat.<session_id>`; conectarse es solo suscribirse.

- **Mismos eventos y mismo orden:** `publish` entrega a todos los suscritos bajo un lock, que fija un único orden.
- **Quien llega a mitad:** la foto y la suscripción se toman a la vez, con el lock que publica los tokens. Recibe el parcial con su `sequence` y sigue desde ahí.
- **Sin turnos dobles:** un lock por sesión los serializa. Si dos pestañas escriben a la vez, la segunda redirige a la primera.
- **Uno lento no frena al resto:** si se le acumulan 5000 eventos se le desconecta y, al volver, se rehidrata.
- **Cero suscriptores** tampoco detiene al agente.

Medido en `test_many_subscribers_see_the_same_events_with_a_single_agent_call`: 12 conexiones (2 llegan a mitad), una interrupción, dos turnos → **2 llamadas al agente** y los mismos eventos para todas.

**El supervisor todavía no puede entrar:** hoy solo conecta el dueño de la sesión (4404 para el resto). El canal ya lo admite; faltaría el permiso por rol y que su conexión sea **de solo lectura**, porque ahora cualquier suscriptor puede interrumpir. Con varios procesos, `ChannelBroker` pasaría a Redis Pub/Sub y cada sesión debería atenderla un solo proceso.

### Abort del stream frente a `interrupt()` HITL

**`interrupt()` no se usa en el chat.** Son cosas distintas:

| | Abort (este PR) | `interrupt()` (aprobación de RFP, Hito 9) |
|---|---|---|
| Qué hace | Termina una generación | Pausa el grafo |
| Para qué | El usuario ya no quiere esa respuesta | Un humano debe decidir para seguir |
| Después | Turno nuevo, otro `message_id` y otra ejecución | Se reanuda la misma ejecución desde su checkpoint |
| Cuándo actúa | En cualquier token, o antes del primero | Solo entre nodos |

Con `interrupt()`, el modelo seguiría generando dentro del nodo: no pararía nada.

**Cómo se aborta:**
1. Marcar la generación y publicar `generation_interrupted` ocurre bajo el mismo lock que publica tokens. Después no puede salir ninguno suyo.
2. `control.cancel()` cancela la tarea y cierra la petición HTTP al modelo, también si aún no llegó el primer token.
3. El grafo se cierra y no ejecuta más nodos. El trace queda como `GenerationAborted`.

**El mensaje parcial** se guarda con exactamente los tokens mostrados y `status = interrupted`. El `UPDATE` exige que siga en `streaming`, así que nada posterior lo reescribe. En la UI sigue visible y marcado, también al recargar.

**El turno siguiente** es otra fila con otro `message_id`, y responde a `new_input`. Empieza cuando el anterior está cerrado. Con `new_input` vacío solo se detiene, y la sesión queda `interrupted` hasta el próximo mensaje.

**La consecuencia menos obvia:** una respuesta cortada podía dejar pendiente una propuesta de memoria que el usuario no llegó a ver, y su siguiente «sí» la aprobaría. Al abortar se descarta.

## Límites

- **Un solo proceso** (BRIEFING §5): el pub/sub y las respuestas en curso viven en memoria. Con varios procesos hay que sustituir `broker.py` por un backplane y fijar cada sesión a un proceso.
- El agente no usa los turnos anteriores para responder (igual que antes): restaurar el hilo restaura la conversación y la propuesta de memoria pendiente.
- `retrieve` y las tools no se pueden cortar a mitad; su resultado se descarta.
- `POST /knowledge/query` y `POST /agent/query` siguen existiendo; la UI ya no usa el primero.

## Pruebas

- `tests/chat/` (74, en CI): contrato, pub/sub, 40 carreras de interrupción, rehidratación, guarda incremental y corte real de la petición al modelo contra un servidor que detecta el cierre.
- `services/api/tests/test_chat_ws.py` (24): el WebSocket en el wire con el grafo real. El CI no ejecuta esta suite.
- `uis/backoffice/src/__tests__/chat.test.ts` (15): estado de la conversación y conexión.
- `scripts/chat_live_smoke.cjs`: agente y modelo reales, en `docs/pr-evidence/chat-websocket/`.
