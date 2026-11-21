# Memoria del agente de soporte de TrackFlow (Hito 8, Parte 1)

**Para quién:** desarrolladores del agente y del harness de la Parte 2, y el equipo de CX de Valentina Cruz, que es quien aprueba lo que el agente recuerda.

**Problema:** los 15 agentes de soporte de Los Ángeles y Zaragoza corrigen al asistente las mismas reglas de carrier una y otra vez, porque no aprende de una sesión a la siguiente.

| Pieza | Dónde |
|---|---|
| Modelo de memoria, guardrails, puerta de auto-evaluación, clasificador de respuestas, store Redis, consolidación y auditoría | `services/agent/memory.py` |
| Llamada única de auto-evaluación (`propuesta_memoria`) y respuesta con memoria aprobada | `services/agent/memory_llm.py` |
| Nodos del grafo (`resolve_pending_memory`, `memory_acknowledge`, `recall_memory`, `propose_memory`) | `services/agent/graph.py` |
| API: `conversation_id`, `memory_proposal` y `memory_resolution` en `POST /agent/query`; `GET /agent/memory`; `GET /agent/memory/audit`; confirmación y rechazo explícitos | `services/api/routes/agent.py` |
| Tests deterministas (fakeredis y dobles del LLM) | `tests/pipelines/test_agent_memory.py`, `services/api/tests/test_agent.py` |
| Evals y ciclos grabados con el LLM real | `tests/pipelines/test_agent_memory_evals.py`, `mock_data/eval/agent-memory-cases.json`, `docs/pr-evidence/agent-memory/` |

## 1. Qué memoria necesita TrackFlow y por qué Redis

Lo que hay que recordar, según el CONTEXT del hito, son **pocos hechos estructurados que se buscan por una clave conocida**:

| Qué | Clave natural | Volumen | Vida útil |
|---|---|---|---|
| Regla de asignación de carrier corregida | carrier + país (8 pares: UPS, FedEx, DHL en EE. UU.; SEUR, MRW, DHL y los transportistas locales en España) | Decenas de zonas | Meses, hasta que alguien la vuelva a corregir |
| Contexto de un incidente recurrente ("la huelga portuaria explica los retrasos de LA esta semana") | país + ruta | Pocos a la vez | Días |
| Preferencia de reporte mensual de un cliente B2B | cliente | Uno por marca | Un año |

**Elección: memoria semántica estructurada sobre Redis**, la clave-valor que ya forma parte del stack como broker de Celery, con persistencia AOF.

- **Búsqueda exacta.** El agente encuentra lo relevante extrayendo de la pregunta el carrier, el país, la ruta o el cliente (`RedisMemoryStore.recall`). No hace falta similitud vectorial: "SEUR / ES" es SEUR / ES, no "algo parecido a SEUR". Una búsqueda vectorial traería reglas de otro carrier por parecido de texto, que es justo el error que se quiere corregir.
- **TTL nativo.** Lo coyuntural, como el contexto de un incidente, caduca solo sin trabajos de limpieza.
- **Escrituras atómicas y auditoría.** Una lista append-only registra cada decisión.
- **Sin infraestructura nueva.** En compose, la memoria usa `redis://redis:6379/2`; Celery usa la base 0.

**Descartados:**
- **Memoria episódica** (guardar conversaciones o turnos): es justo lo que el CONTEXT pide evitar ("consolidar por carrier + país en vez de por ticket individual"). Además arrastraría datos prohibidos, como direcciones o paquetes concretos, dentro de los episodios.
- **VectorDB** (por ejemplo, una colección `*_agent_memory` en Qdrant): no aporta nada con claves exactas y añade el riesgo de que una consulta recupere una regla de otro carrier o país por parecido semántico.
- **Knowledge graph:** las relaciones son triviales (carrier → país → zona); un grafo sería sobreingeniería para menos de 100 entradas.

**Separación del RAG.** La memoria **nunca** escribe en `trackflow_knowledge` ni en Qdrant: usa su propio namespace, `trackflow:agent_memory:*`, en Redis. El RAG sigue siendo de solo lectura, y sus evals de retrieval y sus controles anti-envenenamiento no se mezclan con hechos aprobados por agentes. Lo comprueba `test_memory_never_touches_the_rag_knowledge_collection`.

## 2. Qué se recuerda y qué nunca (CONTEXT.md)

Los tres tipos memorizables son exactamente los del CONTEXT: `regla_carrier`, `contexto_incidente_recurrente` y `preferencia_reporte_b2b`. Cualquier otro tipo se descarta como `PROPUESTA_MAL_FORMADA`.

Lo prohibido lo rechaza **el código** (`check_proposal`), lo pida quien lo pida, y se vuelve a comprobar al confirmar. Si el modelo propone algo prohibido, el usuario ni siquiera ve la propuesta.

| Prohibición del CONTEXT | Código | Cómo se detecta |
|---|---|---|
| Direcciones exactas de clientes **B2C** (destinatarios finales) | `DIRECCION_EXACTA_CLIENTE`, `UBICACION_CLIENTE`, `DATO_PERSONAL_CONTACTO` | Tipo de vía + número, portal o piso, códigos postales de Zaragoza/Aragón y ZIP de EE. UU., coordenadas, "vive en", "domicilio", emails y teléfonos |
| Ubicación de clientes **B2B** (marcas): el CONTEXT exige cubrir los dos tipos | Los mismos | "polígono … nave N", "su almacén está en", "sus oficinas están en" |
| Rutas internas del almacén (seguridad física) | `RUTA_INTERNA_ALMACEN` | Pasillo, estantería, rack, muelle, andén, ruta o recorrido interno, zona de picking, códigos de acceso, puerta de servicio |
| Incidencia puntual de un solo paquete, sin patrón | `INCIDENCIA_PUNTUAL_SIN_PATRON` | Un contexto de incidente exige ≥ 2 tickets o una señal de recurrencia, y no puede citar un tracking, un paquete concreto ni un UUID de ticket |
| Contratos comerciales activos o en negociación (son del CRM) | `CONTRATO_EN_NEGOCIACION` | Contrato, negociación, renovación, propuesta u oferta comercial, tarifa o descuento pactado o negociado, cláusula, RFP, licitación |
| (Integridad) Carrier inexistente o en el país equivocado | `CARRIER_DESCONOCIDO` | Solo los 8 pares carrier-país de TrackFlow |

El criterio es conservador: por ejemplo, "comarca número 4" se bloquea como posible dirección. **Rechazar de más es un fallo seguro** (el agente de soporte puede reformular); dejar pasar una dirección no lo es.

## 3. Interfaz explícita de lectura y escritura (no un system prompt creciente)

```
recall_memory      → MemoryStore.recall(pregunta)        lectura: solo entradas relevantes, máximo 5
propose_memory     → MemoryStore.save_pending(...)       propuesta pendiente: NO es memoria; recall no la lee
resolve_pending    → confirm / edit_and_confirm / reject / discard   la única escritura es confirm o edit_and_confirm, con aprobación del usuario
GET /agent/memory, GET /agent/memory/audit               inspección
```

El estado del grafo no tiene historial de conversación ni prompt acumulado (`test_state_has_no_conversation_history_key`). En cada turno, el modelo solo ve las entradas que `recall` devuelve para esa pregunta, marcadas como "memoria aprobada por X el día Y" (`test_confirmed_memory_is_recalled_next_session_without_growing_a_prompt`).

## 4. Auto-evaluación: criterio explícito y una sola llamada al modelo

```
mensaje ─► resolve_pending_memory ─► receive_question ─► recall_memory (puerta) ─► plan_sources ─► tools / RAG
                                                                                                    │
     puerta cerrada ("nada que recordar") ─── generate / compose_answer / no_context ──► propose_memory (no hace nada)
     puerta abierta ── acuse determinista (o datos de las tools) ──► propose_memory: UNA llamada al modelo
                                                                     {"propuesta_memoria": null | {…}}
                                                                     → guardrails → propuesta al final de la respuesta
```

**Paso 1: puerta determinista** (`evaluate_memory_gate`). Solo se plantea recordar algo si el mensaje trae una **señal de dato nuevo o corregido**:
- corrección: "en realidad", "ya no", "dejó de", "desde el mes pasado", "hay que usar"…;
- recurrencia: "ya van tres tickets", "esta semana", "huelga", "no es un problema nuestro"…;
- preferencia: "siempre quiere", "prefiere", "reporte mensual"…

Además, la señal debe ir sobre **un ancla del dominio memorizable**: carrier, ruta, incidencia, reporte o cliente. Los cierres de conversación se descartan explícitamente. Si la puerta no se abre, no hay ninguna llamada de auto-evaluación.

**Paso 2: el modelo decide** en una sola llamada con salida estructurada `{"propuesta_memoria": null | {tipo, accion, hecho, motivo, …}}` (`reflect_memory`). Recibe la pista de la puerta: tipo candidato, carriers y países mencionados. El código solo completa **claves estructurales** que el mensaje dice literalmente (país, carrier, número de tickets, zona o cliente) y lo deja registrado en el trace (`_campos_completados_por_el_sistema`). El "hecho" lo redacta siempre el modelo.

**Paso 3: guardrails en código** (sección 2) y propuesta **al final de la misma respuesta**, como pregunta:

> 📌 Propuesta de memoria (regla de carrier): «…». Motivo: …. ¿Quieres que lo recuerde para las próximas conversaciones? (propuesta mem-…; no se guarda nada hasta que la confirmes)

**Por qué la respuesta, cuando la puerta está abierta, no la redacta el LLM.** Es una decisión medida con el LLM de desarrollo (`qwen2.5:1.5b`):
- Cuando se le pedían `respuesta` y `propuesta_memoria` en la misma llamada, dejó `propuesta_memoria` en `null` en **6 de 6** intentos con mensajes memorizables (probado con tres prompts distintos, incluido el orden de campos invertido).
- Cuando la salida es solo `propuesta_memoria`, propuso correctamente en los casos medidos.

Hay además un motivo de seguridad. En esas pruebas, el texto libre del LLM afirmaba "SEUR ya no cubre la zona rural de Zaragoza… usa el carrier local" **como si fuera un hecho**, antes de que nadie lo aprobara. Eso es precisamente lo que hay que evitar (ver la sección 7). Por eso, con la puerta abierta, la respuesta es un acuse determinista:

> «Tomo nota de lo que indicas. Todavía no está verificado ni guardado en memoria. Lo que dice hoy la base de conocimiento: «…» (fuente)»

Si hubo tools, la respuesta es la de las tools. En todos los casos hay **como máximo una llamada al modelo por interacción**: la generación normal (puerta cerrada) o la auto-evaluación (puerta abierta). Lo comprueban `test_plain_question_makes_no_proposal_and_one_plain_model_call`, `test_memorable_rag_interaction_proposes_with_a_single_model_call_and_never_writes` y `test_memorable_tool_interaction_uses_a_single_reflection_call`.

### Interacciones que NO deben generar propuesta (documentadas y verificadas)

| Mensaje | Por qué no | Dónde se verifica |
|---|---|---|
| "¿Dónde está el paquete con tracking XJ4471?" | Consulta puntual de tracking, no un patrón (CONTEXT) | Eval `not-memorable-tracking-query` (traza real) |
| "Perfecto, ya quedó resuelto." | Cierre de conversación (CONTEXT) | Eval `not-memorable-closing` |
| "Tradúceme esto al inglés para el cliente." | Tarea de un solo uso (CONTEXT) | Eval `not-memorable-one-off-translation` |
| "¿Qué transportista cubre mejor Aragón rural?" | Pregunta, no aporta un dato | `test_most_interactions_are_nothing_to_remember` |
| "¿En qué estado está el ticket d32a9b66-…?" | Consulta operativa en vivo | Ídem |
| "¿Cuál es la ventana de devolución estándar?" | Política ya documentada en el RAG | Ídem |
| "Gracias, eso es todo." | Cierre | Ídem |

Las que **sí** proponen son los tres ejemplos del CONTEXT (SEUR en la zona rural de Zaragoza, la huelga portuaria de LA y el reporte del cliente de cosméticos), grabados con el LLM real en `mock_data/eval/agent-memory-traces/`.

## 5. Confirmación del usuario y registro auditable

**El siguiente mensaje se evalúa primero contra la propuesta pendiente** (`resolve_pending_memory`, el primer nodo del grafo). El clasificador `classify_pending_reply` produce una etiqueta estructurada (`PendingReplyIntent`):

| Etiqueta | Regla | Ejemplos |
|---|---|---|
| `aprobar` | El mensaje **empieza** por una aprobación clara seguida de un separador o del final | "Sí", "sí, guárdalo", "Vale, adelante.", "Ok, y ¿cuántos tickets abiertos hay?" |
| `rechazar` | Empieza por un rechazo claro seguido de un separador | "No", "No, gracias", "no lo guardes", "No, no lo guardes. ¿…?" |
| `editar` | "Sí, pero …", "guárdalo como: …": el texto sustituye al hecho y se aprueba esa versión, si pasa los guardrails | "guárdalo como: SEUR no cubre la zona rural de Zaragoza desde noviembre" |
| `otro_tema` | Todo lo demás, **incluida la ambigüedad** | "No sé dónde está el paquete XJ4471" (no es un rechazo), "sí hay retrasos en LA?", "Tal vez, luego lo vemos", "La verdad es que sí lo guardaría", cambiar de tema |

No es un match de "sí" en cualquier parte del mensaje: la respuesta debe abrir el mensaje y estar delimitada. Cuando el usuario responde y sigue con otra pregunta ("Sí, guárdalo. Y otra cosa: ¿…?"), se resuelve la propuesta y **el resto se responde con normalidad** en el mismo turno (`remainder`).

**Reglas:**
- **Una sola propuesta pendiente por conversación.** El puntero `session:<id>:pending` lo garantiza: `save_pending` lanza `PendingAlreadyOpen` y `propose_memory` no ofrece una segunda (`PROPUESTA_PENDIENTE_ABIERTA`). Las conversaciones se aíslan por usuario: el `conversation_id` del API se combina con el id del usuario autenticado.
- **El silencio o la ambigüedad nunca aprueban.** `otro_tema` descarta la propuesta (`descartada_sin_respuesta_clara`).
- **Si el usuario nunca responde**, la propuesta caduca a las 24 h y se audita como `caducada_sin_respuesta` la siguiente vez que esa conversación habla con el agente.
- **Auditoría.** Cada decisión va a la lista `trackflow:agent_memory:audit` (consultable con `GET /agent/memory/audit`) con:
  - la fecha y el resultado: `propuesta`, `aprobada`, `editada_y_aprobada`, `rechazada`, `rechazada_por_guardrails`, `descartada_sin_respuesta_clara` o `caducada_sin_respuesta`;
  - la propuesta completa;
  - el **mensaje que la originó** y el **mensaje que la resolvió**;
  - el usuario, la conversación y el `run_id`;
  - las violaciones de guardrails o la deduplicación, si las hubo.

Se registra igual si la propuesta se aprueba, se rechaza o se descarta.

## 6. Consolidación y limpieza: qué olvida el agente

| Mecanismo | Política | Por qué |
|---|---|---|
| Consolidación por clave | Una entrada por **carrier + país** (como mucho 8 pares), una por país + ruta para incidentes y una por cliente B2B. Dentro de cada entrada, un hecho por zona o aspecto, y una corrección de la misma zona **sustituye** a la anterior | Lo sugiere el CONTEXT: las reglas no se fragmentan en decenas de entradas sueltas |
| Deduplicación | El mismo hecho, normalizado, no crea otro registro: suma `confirmaciones` | Varios agentes confirmando lo mismo refuerzan el hecho, no lo duplican |
| Tope por entrada | 12 zonas por carrier-país, 3 por incidente, 6 aspectos por cliente; al superarlo se descarta el hecho aprobado hace más tiempo | Que la memoria no crezca sin control |
| Caducidad de reglas de carrier | 180 días desde la última confirmación | Las coberturas cambian; una corrección vieja que nadie reconfirma es más peligrosa que no tenerla. Basta con que alguien la vuelva a confirmar para renovarla |
| Caducidad de incidentes | TTL de 7 días (como mucho 30) en Redis | Una huelga explica los retrasos de esta semana, no los del trimestre que viene |
| Caducidad de preferencias B2B | 365 días sin uso | Cambian poco, pero un cliente que se fue no debe ocupar memoria |
| Tope de clientes | 60 clientes B2B como máximo; se expulsa el menos usado (LRU, según `recall`) | Acota la memoria |
| Propuestas pendientes | 24 h | El silencio no aprueba |

`cleanup()` es idempotente y se ejecuta en cada `recall` y en cada escritura, así que no hace falta un cron.

## 7. Envenenamiento: una "corrección" falsa no se convierte en memoria sin más

1. **La puerta y los tipos cerrados:** solo se puede proponer uno de los tres tipos, sobre carriers y países reales de TrackFlow (`CARRIER_DESCONOCIDO`).
2. **Los guardrails en código** se aplican al proponer **y al confirmar**: una edición o un pendiente manipulado en Redis también se revisa (`test_confirm_reapplies_guardrails_even_if_the_pending_was_tampered`).
3. **El agente no da por cierta una corrección no aprobada:** con la puerta abierta, la respuesta es un acuse determinista que muestra **lo que dice la base de conocimiento**. Quien aprueba ve la contradicción (por ejemplo, "SEUR: mejor cobertura en zonas rurales de Aragón") antes de decir que sí.
4. **Aprobación humana explícita y atribuida:** nada se escribe sin una etiqueta `aprobar` o `editar` del usuario autenticado, y queda en la auditoría quién lo aprobó, qué mensaje lo originó y cuándo.
5. **Trazabilidad en el uso:** cuando la memoria se usa, aparece como "memoria aprobada por X el día Y", no como un hecho de la base de conocimiento. La memoria nunca se escribe en el RAG.
6. **Caducidad:** una regla falsa que nadie reconfirma desaparece a los 180 días; un incidente falso, en 7.

Queda un riesgo abierto: un agente de soporte con credenciales válidas puede aprobar una regla falsa a propósito. Mitigarlo del todo requeriría un segundo aprobador o un rol `manager` para las reglas de carrier; es una decisión para la Parte 2 o para Valentina Cruz, no algo que el agente deba decidir.

## 8. Por qué no hace falta una arquitectura multi-agente

Todo lo anterior es **un único grafo** con estado explícito. La auto-evaluación es una llamada estructurada del mismo modelo (`propuesta_memoria`), que solo ocurre cuando una puerta determinista lo justifica. La validación es código (guardrails) y la decisión es del usuario (clasificador de respuestas).

Un agente "crítico" o "memorizador" separado añadiría otra llamada al modelo por interacción, otro prompt que mantener y un punto de desacuerdo entre agentes. Además no resolvería lo difícil (qué está prohibido y quién aprueba), que se resuelve mejor con reglas verificables que con otro LLM.

## 9. Evidencia

`docs/pr-evidence/agent-memory/` contiene los ciclos completos grabados con la pila real (LLM, RAG, servidor MCP y Redis):

- **Aprobado:** una propuesta de regla de carrier, el "Sí, guárdalo." del usuario y una **conversación nueva** al día siguiente que recupera `carrier:SEUR:ES` y lo usa en la respuesta.
- **Rechazado:** una propuesta de preferencia B2B, el "No, no lo guardes. ¿Cuál es la ventana de devolución estándar?" del usuario, la memoria **idéntica** antes y después, y la pregunta del mismo mensaje respondida con normalidad.
- **Cambio de tema:** una propuesta sobre un incidente que el usuario ignora al preguntar otra cosa, descartada por defecto.

Cada ciclo incluye, por turno, la memoria antes y después y la auditoría completa. Los evals `tests/pipelines/test_agent_memory_evals.py` verifican esos archivos.
