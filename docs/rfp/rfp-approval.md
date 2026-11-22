# Aprobación humana por departamento y cierre del documento (Hito 9, Parte 3)

**Para quién:** el equipo comercial de Miguel Torres, los responsables de departamento (Ana Whitfield, Carlos Vega, Sofía Ramos) y quien opere el flujo en producción. Este documento explica cómo se aprueba cada sección, cómo se resuelven los conflictos entre departamentos, cuándo se genera el documento final y cómo saber qué agente hizo qué.

> En este repo, el `data/` del BRIEFING es `mock_data/`. La Parte 3 extiende el mismo pipeline (`mock_data/pipelines/rfp_intake/`), el mismo backend (`services/api`, router `/rfp-intake`) y la misma UI (`uis/backoffice`, página `/rfps`). No hay API nueva.

## Layout

| Pieza | Dónde |
|---|---|
| Grafo de aprobación, arbitraje y cierre | `mock_data/pipelines/rfp_intake/approval_graph.py` |
| Reglas: validación de la respuesta humana, triggers de §7 y documento final | `mock_data/pipelines/rfp_intake/approval.py` |
| Checkpointer y candado por ticket | `mock_data/pipelines/rfp_intake/checkpointing.py`: `PostgresSaver` (Supabase), o `SqliteSaver` en desarrollo y tests |
| Trazabilidad (las tres partes) | `mock_data/pipelines/rfp_intake/tracing.py` y la tabla `rfp_trace_events` |
| Persistencia | Migración `20261122200000_rfp_approval.sql`: columnas de aprobación en ticket y sección, `rfp_final_documents` y `rfp_trace_events`. Las tablas del checkpointer las crea `PostgresSaver.setup()`. |
| Trabajo asíncrono | Celery: `rfp_approval.start` (encolada al terminar la Parte 2) y `rfp_approval.resume` (una por decisión) |
| HTTP | `POST /rfp-intake/tickets/{id}/sections/{department_id}/decision`, `POST …/approval/start` (reintento), `GET …/final-document[?format=md]` y `GET …/trace` |
| UI | En `/rfps`: `ApprovalPanel` por departamento, `FinalDocumentPanel` y `TracePanel` |
| E2E reproducible | `tests/pipelines/test_rfp_e2e.py` (Partes 1→3 con fixtures y resumes simulados), `scripts/rfp_e2e.py` (contra Supabase, con PDF real o fixture) y `scripts/rfp_ui_approval.cjs` (por la UI) |

## El flujo como un solo proceso

| Parte | Estado del ticket | Lo que pasa |
|---|---|---|
| 1 | `analizando` → `analisis_completo` (o `descartado`) | Recepción, clasificación y handoff `rfp_handoff.v1` |
| 2 | `generando_borrador` → `en_evaluación` → `esperando_aprobación` (o `needs_human_review` si alguna sección agotó el límite) | Generadores por departamento, evaluadores en paralelo y handoff `rfp_response_handoff.v1` |
| 3 | `esperando_aprobación` mientras falte **alguna** aprobación → `terminado` | Aprobación por departamento, arbitraje y documento final |

Cada parte encola la siguiente en el worker; ninguna espera un clic para arrancar. `approval_stage` indica en qué está la Parte 3:
- `iniciando`, `en_curso`: el worker está trabajando y la UI refresca sola;
- `esperando`: hay al menos una decisión humana pendiente;
- `completado`: el documento final está guardado;
- `bloqueado`: no habrá documento final sin intervención humana; el ticket pasa a `needs_human_review` con el motivo en `approval_note`;
- `fallido`: error técnico; `last_error` lo explica y Celery reintenta.

Si una sección agotó el límite en la Parte 2, el ticket pasa por `needs_human_review` antes de `esperando_aprobación`: la sección llega a su responsable marcada como **provisional**.

## Grafo e interrupciones

```
START → start ─Send×N─► department_approval ─┐            hilo: rfp-{ticket_id}:approval-{run}
                                            ▼
                                        arbitrate ─┬─ sin conflictos ──────────────► final_document → END   (terminado)
                                                   ├─ trigger §7 y quedan rondas ─Send─► department_approval (solo afectados)
                                                   └─ rechazo final / rondas agotadas ─► block → END        (needs_human_review)

department_approval = subgrafo de UNA rama (namespace propio dentro del checkpoint):
  prepare ─┬─ revisar → revise (generador de la Parte 2 de ESE departamento + 3 evaluadores) ─┐
           └──────────────────────────────────────────────────────────────────────────────┴─► request_approval
  request_approval → await_decision ⏸ interrupt() → apply_decision ─┬─ aprobar → fin de la rama
                                                                    ├─ cambios / rechazo (quedan rondas) → revise
                                                                    └─ rechazo con el límite agotado → fin (rechazada)
```

- **La pausa es por rama.** El `interrupt()` vive dentro del subgrafo de cada departamento. Cuando `lastmile` está listo y `warehouse` no, `lastmile` se pausa en su punto de aprobación y `warehouse` sigue sin esperar a nadie.
- **Reanudar es un punto de entrada explícito.** `resume_approval(ticket, department, decisión)` busca en el checkpoint la interrupción de **ese** departamento y ejecuta `Command(resume={interrupt_id: decisión})`. Solo avanza esa rama: si pide cambios, se regenera y vuelve a pausarse con una versión nueva mientras las demás siguen pausadas en la suya. Los nodos previos a la pausa no se repiten; la traza lo verifica: `start` se ejecuta una sola vez y hay una `request_approval` por versión.
- **El checkpointer guarda el estado antes de cada interrupción.** Se usa `PostgresSaver` sobre Supabase (conexión psycopg 3 sin prepared statements, compatible con el pooler en modo transacción). En desarrollo y tests, `SqliteSaver`; nunca el checkpointer en memoria fuera de los tests.
- **`thread_id` por ticket:** `rfp-{ticket_id}:approval-{run}`. Dos tickets nunca comparten checkpoint, y las ramas por departamento quedan separadas por el namespace de su subgrafo dentro del hilo. Un candado por ticket (`pg_advisory_xact_lock`) evita que dos workers reanuden a la vez el mismo hilo. Los tickets distintos no se bloquean entre sí.

## Validación de la respuesta humana

Antes de que la decisión entre al grafo, la validan tres puntos:
- la API, que responde `422` con los motivos;
- `resume_approval`, en el worker;
- el propio nodo, que vuelve a interrumpir con `validation_errors` si recibiera algo inválido.

`validate_decision` comprueba:
- **Forma:** `decision` ∈ {`approve`, `reject`, `request_changes`}, la versión, el comentario y los compromisos.
- **Responsable:** solo el responsable del BRIEFING §2.1 decide su sección (Ana Whitfield `warehouse`, Carlos Vega `lastmile`, Sofía Ramos `reverse`). El usuario autenticado que registra la decisión queda en `decided_by`.
- **Versión:** la decisión debe referirse a la versión pendiente. Una aprobación de un borrador viejo se rechaza.
- **Comentario:** obligatorio (al menos 10 caracteres) para pedir cambios o rechazar, porque es el feedback que recibe el generador.
- **Compromisos estructurados permitidos:** `warehouse` → `capacity_monthly_orders`, `capacity_sku`; `lastmile` → `monthly_volume`.
- **Edición al aprobar:** se permite, pero el texto editado debe pasar el evaluador de cumplimiento de §5.
- **Límite:** al agotar las rondas de cambios, `request_changes` deja de estar permitido.

## Límites de iteraciones

| Ciclo | Límite | Al agotarlo |
|---|---|---|
| Generador ↔ evaluadores (Parte 2, también en las revisiones de la Parte 3) | `RFP_MAX_ITERATIONS` (3) | Sección provisional (`needs_human_review`) que pasa igual a su responsable |
| Rondas de cambios por departamento | `RFP_MAX_APPROVAL_ROUNDS` (3) | Solo se puede aprobar o rechazar. Un rechazo deja la sección `rechazada` y el ticket bloqueado. |
| Ciclo entre departamentos (arbitraje → revisión → reaprobación) | `RFP_MAX_ARBITRATION_ROUNDS` (2) | Ticket bloqueado (`needs_human_review`) sin documento final; el conflicto queda registrado con su árbitro |

## Arbitraje (BRIEFING §7)

`arbitrate` es un nodo dedicado. Se ejecuta cuando todas las ramas activas terminaron y trabaja sobre **hechos estructurados** que cada rama registra al aprobarse (`section_facts`). No lee texto libre ni pide a un LLM que vote.

| Trigger | Hecho estructurado | Árbitro fijo | Resolución que aplica el nodo |
|---|---|---|---|
| `volume-vs-capacity` | `warehouse.capacity_monthly_orders` (compromiso de Ana Whitfield al aprobar) < `lastmile.assumed_monthly_volume` (compromiso de Carlos Vega o volumen de la RFP) | Miguel Torres | Limita el volumen de la propuesta a la capacidad (override `monthly_volume`) y devuelve `lastmile` a su generador para revisar volumen y costo a la baja; después, nueva aprobación de Carlos Vega |
| `returns-sla-breach` | `returns_promises_under_48h` de cada sección: frases sobre devoluciones con un plazo menor de 48 h (detector del evaluador de §5.3) | Sofía Ramos si solo la incumple `reverse`; Miguel Torres si otra sección la embebe | `request_changes` forzado en cada sección ofensora; sin documento final hasta cumplir |
| `currency-mismatch` | `currency` declarada por sección y `currencies_in_content` (USD/EUR detectados en el texto), frente a `CURRENCY_BY_COUNTRY[client_country]` | Miguel Torres | Reescribir las secciones ofensoras en la moneda del país; si no se resuelve tras el límite de rondas, se rechaza (bloqueo) |

Cada arbitraje se registra en `rfp_tickets.arbitration`. Incluye el trigger, el árbitro, la evidencia estructurada, los departamentos afectados, la resolución y la ronda. Aparece en la UI, en el documento final y en la solicitud de aprobación de la sección revisada.

## Documento final

Cuando todas las ramas activas están aprobadas y el arbitraje no encuentra conflictos, `final_document` guarda el **FinalDocument** de §2.3 en `rfp_final_documents`:
- `ticket_id`;
- `sections`: las secciones aprobadas, con su responsable, fecha y versión;
- `currency`: la del país del cliente;
- `generated_at`;
- las aprobaciones y los arbitrajes;
- el contenido en Markdown: título con el cliente, referencia RFP, moneda, tabla de aprobaciones, arbitrajes resueltos, pedidos fuera de alcance y las secciones aprobadas.

El ticket pasa a `terminado`. El documento se ve en la UI y se descarga con `GET …/final-document?format=md`.

## Trazabilidad

Cada ejecución de nodo de las tres partes registra el agente, el input, el output y los timestamps de inicio y fin. Se guarda en dos sitios:
- **en el estado del grafo** (clave `trace`, que el checkpoint conserva);
- **en `rfp_trace_events`**, para reconstruir el orden aunque el grafo siga pausado o haya fallado.

Agentes registrados:
- **Parte 1:** `markitdown_converter`, `metadata_extractor`, `classifier`, `orchestrator`, `{departamento}_worker`, `synthesizer`, `router`.
- **Parte 2:** `response_coordinator`, `{departamento}_generator`, `readability_evaluator`, `relevance_evaluator`, `compliance_evaluator`, `evaluation_judge`, `response_assembler`.
- **Parte 3:** `approval_coordinator`, `approval_gate`, `human:{responsable}`, `arbiter:Miguel Torres`, `document_assembler`.

La UI lo muestra en «Trazabilidad: agentes y pasos» y la API en `GET …/trace`.

## Preguntas de diseño

**¿Qué pasa si un departamento rechaza su sección después de la interrupción? ¿Vuelve al generador de la Parte 2 o requiere una nueva ejecución?**

Vuelve al generador de la Parte 2 **dentro de la misma ejecución y del mismo hilo**, sin reiniciar nada. La rama de ese departamento pasa por `revise`, que ejecuta el ciclo generador-evaluadores de la Parte 2 con el agente de **ese** departamento, y vuelve a pausarse con una versión nueva:
- **Pedir cambios:** el generador recibe el comentario del responsable como feedback y parte del borrador anterior.
- **Rechazar:** el generador reescribe la sección desde cero con el motivo del rechazo.

Las demás ramas no se enteran. Cada vuelta consume una ronda (`RFP_MAX_APPROVAL_ROUNDS`). Un rechazo con las rondas agotadas deja la sección `rechazada`, y el ticket queda bloqueado (`needs_human_review`, sin documento) a la espera de que Miguel Torres decida con el responsable. Solo entonces hace falta una ejecución nueva (`POST …/approval/start`, en un hilo nuevo `…:approval-{run+1}`).

**¿Qué información mínima necesita ver un humano en el punto de aprobación para decidir sin releer todo el documento?**

La solicitud de aprobación (`approval_request`, el valor del `interrupt`) contiene exactamente esto:
- versión y ronda (`v2 · ronda 1/3`) y **por qué existe esta versión**: el feedback o el arbitraje que la produjo;
- cliente, país y **moneda** que debe usar;
- el resultado de la evaluación automática: legibilidad, pertinencia y cumplimiento con sus `rule_id` y la frase exacta que incumple;
- si es **provisional**;
- los **importes pendientes** de la sección («Pendiente de cotizar por Carlos Vega»), que son lo que el responsable debe completar o asumir;
- las cifras clave de la RFP (volumen, SKU, plazo) y las preguntas abiertas al cliente;
- los arbitrajes que afectan a su sección;
- los compromisos que puede registrar (capacidad o volumen).

El borrador completo está debajo, pero para decidir basta con esa cabecera.

**¿Cómo detectas los triggers de conflicto en estado estructurado, para que el nodo de arbitraje sea real y no decorativo?**

Cuando una rama se aprueba, `apply_decision` calcula los hechos de esa sección (`section_facts`):
- la moneda declarada y las monedas que aparecen en el texto;
- las promesas de devoluciones por debajo de 48 h (frase y plazo);
- en `warehouse`, la capacidad comprometida (un **entero** que el responsable registra al aprobar, validado);
- en `lastmile`, el volumen asumido (su compromiso o el volumen estructurado de la RFP).

El nodo `arbitrate` compara esos campos. Por ejemplo, `capacity_monthly_orders < assumed_monthly_volume` dispara `volume-vs-capacity`. Si dispara, el nodo **cambia el flujo**: envía solo las ramas afectadas de vuelta a su generador, con un override estructurado (el volumen limitado a la capacidad) y el feedback del árbitro, y no genera el documento final hasta que el conflicto desaparece o se agotan las rondas. Los tests lo prueban con estado sembrado (`test_conflict_triggers_are_detected_on_structured_state`) y con el grafo completo (volumen frente a capacidad, devoluciones en menos de 48 h y límite de rondas de arbitraje).

## Pruebas

- **`tests/pipelines/test_rfp_approval.py`** (11 tests). Cada uno siembra el estado con las Partes 1 y 2 reales sobre los fixtures y usa un checkpointer SQLite:
  - interrupción y reanudación hasta el documento final;
  - reanudar no reinicia;
  - **B aprobado mientras A sigue interrumpido**, con aserción sobre la traza;
  - validación de la respuesta humana;
  - límite de rondas de aprobación y límite de rondas de arbitraje;
  - arbitraje `volume-vs-capacity` y `returns-sla-breach`, y los tres triggers sobre estado estructurado;
  - tickets concurrentes con hilos distintos;
  - trazabilidad.
- **`tests/pipelines/test_rfp_e2e.py`:** las Partes 1→3 con ModaViva y con Luna, con aprobaciones simuladas. Verifica que el ticket recorre exactamente `analizando → analisis_completo → generando_borrador → en_evaluación → esperando_aprobación → terminado`, que los contratos se encadenan y que los datos (cliente, moneda, departamentos y preguntas abiertas de la Parte 1) llegan al documento final.
- **`services/api/tests/test_rfp_intake_api.py`:** una decisión inválida devuelve `422`; una válida devuelve `202` y encola; la reanudación lleva al documento final (JSON y `.md`) y la traza queda disponible.
- **Prueba real:** `scripts/rfp_e2e.py` contra Supabase con el checkpointer de Postgres, y `scripts/rfp_ui_approval.cjs` por la UI. La evidencia está en `docs/pr-evidence/rfp-approval/`.
