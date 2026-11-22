# Generación y evaluación de respuestas a RFP (Hito 9, Parte 2)

**Para quién:** el equipo comercial de Miguel Torres, los responsables de departamento (Ana Whitfield, Carlos Vega, Sofía Ramos) y quien implemente la Parte 3. Este documento explica cómo se genera cada sección de la propuesta económica, cómo se evalúa y qué recibe la Parte 3.

> En este repo, el `data/` del BRIEFING es `mock_data/`. La Parte 2 extiende el mismo pipeline (`mock_data/pipelines/rfp_intake/`), el mismo backend (`services/api`) y la misma UI (`uis/backoffice`, página `/rfps`). No hay API nueva.

## Layout

| Pieza | Dónde |
|---|---|
| Agentes generadores, **uno por departamento**, cada uno en su módulo | `mock_data/pipelines/rfp_intake/generators/warehouse.py` (`WarehouseGenerator`), `lastmile.py` (`LastMileGenerator`) y `reverse.py` (`ReverseGenerator`); base común y entrada desde el handoff en `generation.py` |
| Agentes evaluadores (legibilidad, pertinencia, cumplimiento) | `mock_data/pipelines/rfp_intake/evaluators.py` |
| Grafo y ciclo generador-evaluador | `mock_data/pipelines/rfp_intake/response_graph.py` |
| Tarifario de TrackFlow (sale de la base de conocimiento) | `mock_data/pipelines/rfp_intake/rate_card.py` |
| Persistencia | Columnas nuevas en `rfp_tickets` y `rfp_department_sections`, y la tabla `rfp_section_evaluations` (migración `supabase/migrations/20261122150000_rfp_response_generation.sql`) |
| Trabajo asíncrono | El mismo worker de **Celery**: tarea `rfp_response.generate_ticket`, encolada sola al terminar la Parte 1 |
| HTTP | `POST /rfp-intake/tickets/{id}/generate` (regenerar). `GET /rfp-intake/tickets/{id}` añade borradores, `EvaluationResult` e historial |
| CLI | `scripts/rfp_intake.py generate <ticket_id> [--max-iterations N]` |

## Entrada: el handoff de la Parte 1

La Parte 2 **solo arranca** desde tickets que la Parte 1 dejó listos:
- `status = analisis_completo`;
- en `rfp_tickets.handoff`, el contrato `rfp_handoff.v1` con `next_status = "generando_borrador"`.

Al terminar la Parte 1, el worker encola la generación (`RFP_AUTO_GENERATE=0` lo desactiva). Si no hay contrato válido, `section_inputs_from_handoff` lanza `HandoffError` y no se genera nada.

Cada generador recibe un `SectionInput` construido **solo** con el handoff:
- `ticket_id`;
- el workstream de su departamento: `key_aspects`, `ask` y `open_questions` del synthesizer;
- el cliente: nombre, país, moneda y contacto;
- los metadatos compartidos (volumen, SKU, plazo, presupuesto y servicios excluidos).

**Nunca relee el PDF ni el Markdown.** Un test lo verifica: el prompt no contiene el texto de la RFP y el PDF del ticket ni siquiera existe.

## Un agente generador por departamento

Cada departamento tiene su propio agente: una clase en su propio módulo y registrada en `generators.GENERATORS`. El grafo elige `generator_for(department_id)`. Cada agente define:
- **`scope`:** su alcance en el prompt.
  - `warehouse`: factura por m³, nunca «por pallet» ni «por SKU», y no habla de devoluciones ni de transporte.
  - `lastmile`: costo final al cliente y nunca tarifas de un transportista.
  - `reverse`: nunca menos de 48 h.
- **`concept_rows`:** sus conceptos económicos.
- **`knowledge`:** los datos de la base de conocimiento que le corresponden.
- **`extra_service_lines`:** sus compromisos de servicio propios.

Un agente rechaza redactar la sección de otro departamento. Los alcances separados nacieron de la primera prueba real: con un único prompt genérico, el generador de `warehouse` habló de devoluciones en menos de 48 h y de «16 EUR por pallet o SKU».


Cada sección sigue el formato del BRIEFING:
- **Qué aporta cada departamento (§2.1):**
  - `warehouse`: capacidad, costo por pallet/SKU y onboarding;
  - `lastmile`: costo por envío, transportistas por destino y SLA;
  - `reverse`: costo y tiempo de procesamiento de devoluciones.
- **Lineamientos de §5:** moneda, SLA, descuentos por volumen, 48 h mínimas para devoluciones y ninguna tarifa de transportista.

```
## <Departamento> — Propuesta económica
Cliente · País · Moneda · Responsable TrackFlow
### Alcance y enfoque            ← lo redacta el LLM a partir de los key_aspects (y del feedback si es un reintento)
### Conceptos económicos (<moneda>)    ← tabla según §2.1
### Descuentos por volumen       ← tabla por tramos (§5)
### Nivel de servicio            ← SLA de entrega a tiempo 90 % (§5); devoluciones nunca en menos de 48 h
### Supuestos y preguntas abiertas    ← open_questions de la Parte 1
```

- **Idioma:** el del cliente. España → español; EE. UU. → inglés.
- **Moneda:** la del país. España → EUR; EE. UU. → USD.
- **Las cifras salen solo de dos fuentes:**
  - el handoff (los datos de la RFP);
  - `rate_card.py`, que transcribe la base de conocimiento: 18 USD / 16 EUR por m³ al mes, 30 días de gracia, recargo del 150 % desde el día 181, SLA del 90 % con compensación del 5 %, transportistas por país, ventana de devolución de 30 días y procesamiento de al menos 48 h.
- **Lo que la base de conocimiento no publica no se inventa.** El costo por envío, el costo por devolución y el onboarding quedan como «Pendiente de cotizar por \<responsable\>». Los porcentajes de descuento quedan «Pendiente de aprobación de Miguel Torres», porque según la base de conocimiento un account manager no puede ofrecer descuentos por su cuenta. El responsable completa esos valores al aprobar la sección en la Parte 3.
- **Opcional (base de conocimiento):** el generador usa los datos de la base de conocimiento ya transcritos en `rate_card.py`, en lugar de una consulta semántica a Qdrant en cada sección. Son cuatro documentos cortos y cifras exactas: transcribirlos da trazabilidad (cada cifra tiene su fuente) y evita que la recuperación cambie una tarifa.
- **Sin LLM**, la parte narrativa se escribe con los `key_aspects` y se marca `generator_meta.narrative = "determinista"`.

## Evaluadores en paralelo

En el subgrafo de cada sección, `generate` abre tres ramas en paralelo y `judge` espera a las tres:

```
generate ─┬─► eval_readability ─┐
          ├─► eval_relevance  ──┼─► judge ─┬─ pasa / límite agotado → fin
          └─► eval_compliance ─┘           └─ falla y quedan iteraciones → generate (con feedback_for_generator)
```

| Evaluador | Recibe | Revisa |
|---|---|---|
| **Legibilidad** | El texto de la sección (sin tablas ni encabezados) y el idioma | Fernández-Huerta ≥ 55 en español; Flesch de py-readability-metrics ≥ 40 en inglés; ninguna oración de más de 30 palabras. Guarda también Flesch-Kincaid y Gunning Fog de py-readability-metrics cuando hay al menos 100 palabras. |
| **Pertinencia** | La sección, la parte narrativa y lo que pidió la RFP según el handoff | Que responda a cada `ask` del departamento (§2.1); que traiga las cifras de la RFP (p. ej. «15000 SKU», «5000 pedidos/mes»); que traslade las preguntas abiertas de la Parte 1; que no use cifras ausentes de la RFP y del tarifario; que no ofrezca un servicio que el cliente excluyó (ModaViva: última milla). |
| **Cumplimiento** | La sección y la moneda esperada del país | Las cinco reglas de §5, cada una con su `rule_id` (tabla siguiente). |

| `rule_id` | Regla (§5) | Cómo se detecta |
|---|---|---|
| `BRIEFING-5.1-currency` | USD para EE. UU. y EUR para España | Falta la moneda esperada, aparece la otra (USD, $, EUR, €…) o el país es desconocido |
| `BRIEFING-5.2-on-time-sla` | Indicar el SLA de entrega a tiempo (%) | No hay «SLA … NN %» ni «NN % de entregas a tiempo» |
| `BRIEFING-5.3-returns-min-48h` | Nunca devoluciones en menos de 48 h | En una frase o celda sobre devoluciones: horas < 48, «1 día», «mismo día» o «menos de 48 h», sin negación delante («nunca en menos de 48 horas» no es una violación) |
| `BRIEFING-5.4-volume-discount-table` | Tabla de descuentos por volumen | Un encabezado de descuentos seguido de una tabla con al menos dos tramos |
| `BRIEFING-5.5-no-carrier-rates` | No revelar tarifas negociadas con transportistas | Un transportista concreto (UPS, FedEx, DHL, MRW, SEUR…) junto a un importe o a «tarifa negociada» |

**`EvaluationResult`**, persistido por iteración en `rfp_section_evaluations`; el último se guarda también en `rfp_department_sections.evaluation_results`:

```
section_id: "<ticket_id>:<department_id>"
department_id, iteration
readability: { pass, score, details { metric, threshold, avg_words_per_sentence, long_sentences[], py_readability } }
relevance:   { pass, missing_aspects[], unsupported_figures[], off_scope[] }
compliance:  { pass, rule_ids[], violations[ { rule_id, message, evidence, fix } ], rules_checked[] }
overall_pass: bool
feedback_for_generator: string    # concreto: regla, frase exacta y cómo corregirla
```

## Ciclo generador-evaluador y límite de iteraciones

- Si una sección falla, `judge` la devuelve **solo a su generador**, junto con el `feedback_for_generator` y su borrador anterior. El resto de secciones sigue su curso.
- **Límite:** 3 iteraciones por sección. Se configura con `RFP_MAX_ITERATIONS` o con `--max-iterations` en el CLI. El BRIEFING (§3) apunta a una media de menos de 2 iteraciones por sección; el handoff publica `avg_iterations_per_section` para medirla.
- **Al agotar el límite sin pasar:**
  - se conserva el último borrador y su `EvaluationResult`;
  - la sección queda `section_status = needs_human_review` (`needs_human_review = true`) y el ticket queda en `needs_human_review`;
  - la sección **se incluye igual** en el handoff a la Parte 3;
  - el ticket **nunca se descarta**.

## Estado del ticket

| Momento | `status` | `response_stage` |
|---|---|---|
| La Parte 1 terminó | `analisis_completo` | `generando` (encolado) |
| Generadores escribiendo | `generando_borrador` | `generando` |
| Hay un borrador y lo están evaluando (incluye los reintentos del ciclo) | `en_evaluación` | `evaluando` |
| Todas las secciones pasaron | `esperando_aprobación` | `handoff` |
| Alguna sección agotó el límite | `needs_human_review` | `handoff` |
| Error técnico (LLM o base de datos caídos) | se conserva | `fallido` + `last_error`; Celery reintenta y la UI ofrece «Regenerar borradores» |

- **Estado por sección:** cada sección tiene el suyo (`generando` → `en_evaluacion` → `evaluacion_superada` | `needs_human_review`).
- **Estado del ticket cuando todo pasa:** queda en `esperando_aprobación`, porque lo siguiente es la pausa humana por departamento de la Parte 3, con cada sección en `approval_status = pendiente` y su `approver`. La aprobación en sí la implementa la Parte 3.

## Handoff a la Parte 3 (`rfp_response_handoff.v1`)

Se guarda en `rfp_tickets.response_handoff` y contiene:
- **Identificación:** `ticket_id`, `rfp_id`, `run` y `next_status: "esperando_aprobación"`.
- **Revisión humana:** `needs_human_review` y `needs_human_review_sections`.
- **Métricas del ciclo:** `max_iterations` y `avg_iterations_per_section`.
- **Cliente:** `client` y `currency`.
- **`sections`:** por cada departamento activo:
  - `department_id`, `approver`, `section_status`, `provisional`, `iterations`;
  - `currency` y `language`;
  - `draft_content` y `evaluation_result`;
  - `iteration_history`.
- **Arrastrados de la Parte 1:** `conflicts` y `out_of_scope_requests`, que el nodo de arbitraje de la Parte 3 usará junto con la moneda por sección (trigger `currency-mismatch`).

## Preguntas de diseño

**¿Qué información del estado necesita realmente cada evaluador? ¿Recibe la sección o el documento completo?**

Solo la sección, más lo mínimo para juzgarla:
- **Legibilidad:** el texto de la sección y el idioma.
- **Pertinencia:** la sección y, del handoff, lo que pidió la RFP: los `ask` del departamento, los `key_aspects` con cifras, las `open_questions` y los servicios excluidos.
- **Cumplimiento:** la sección y la moneda que corresponde al país.

Ninguno recibe el documento completo ni las secciones de otros departamentos. Así son más baratos, no se contaminan con otra sección (por ejemplo, el «24 a 48 horas» de entrega express de `lastmile` no cuenta como promesa de devoluciones) y un fallo apunta a una sección concreta. Las contradicciones *entre* secciones (moneda, volumen frente a capacidad) son de la Parte 3: allí el nodo de arbitraje compara las secciones del handoff.

**¿Cómo evitas que dos evaluadores en paralelo entren en conflicto al escribir en el estado compartido?**

Cada evaluador escribe **una clave distinta** del estado del subgrafo (`readability`, `relevance`, `compliance`) y ninguno la lee ni la modifica en otro evaluador. LangGraph rechaza dos escrituras concurrentes a la misma clave sin reductor, así que un conflicto daría error en lugar de perderse en silencio.

`judge` es el único nodo que une los tres resultados. Se ejecuta cuando han terminado los tres, porque la arista es `["eval_readability", "eval_relevance", "eval_compliance"] → judge`. También es el único que persiste. En el grafo externo, cada departamento corre en su propio subgrafo y devuelve su resultado a una lista con reductor (`operator.add`). En la base de datos, cada escritura es una transacción propia sobre su fila (`ticket_id`, `department_id`) y el repositorio serializa las escrituras dentro del proceso.

**Cuando una sección llega a `needs_human_review`, ¿cómo se lo muestras a Ventas para que sepa qué borrador es provisional?**

En `/rfps` lo indican tres capas:
- **La lista:** el ticket aparece con el estado naranja «Revisión humana».
- **El aviso del detalle:** una franja «Revisión humana necesaria antes de enviar la propuesta» nombra los departamentos con borrador provisional.
- **Cada sección provisional:**
  - un borde ámbar y la etiqueta «Borrador provisional · revisión humana · iteración 3/3»;
  - el aviso «PROVISIONAL: este borrador no superó la evaluación tras 3 iteraciones…»;
  - el resultado de los tres evaluadores, con los `rule_id` y la frase exacta que incumple;
  - el feedback que recibió el generador;
  - el historial de iteraciones.

Para la Parte 3, la sección viaja con `provisional: true` y su `EvaluationResult`, de modo que el responsable no la aprueba a ciegas.

**¿El feedback que recibe el generador es lo bastante específico para corregir el problema real, o es genérico?**

Es específico. Cada línea lleva tres cosas:
- la regla o el evaluador (`[BRIEFING-5.3-returns-min-48h]`, `[pertinencia]`, `[legibilidad]`);
- **la frase exacta** del borrador;
- **cómo corregirla**.

Ejemplo real de un test: «[BRIEFING-5.3-returns-min-48h] Promete procesar devoluciones en menos de 48 horas. Texto: «Procesamos cada devolución en 24 horas.». Cómo corregirlo: Elimina ese plazo: el procesamiento de devoluciones nunca es inferior a 48 horas.».

Lo mismo con las cifras inventadas («Cifras que no están en la RFP ni en el tarifario: 12000…»), las oraciones largas (se citan) o el servicio excluido. El generador recibe ese texto junto con su borrador anterior y la instrucción de corregir exactamente eso y conservar lo demás. Hay un test que verifica que el feedback persistido es literalmente el que llegó al prompt del reintento.

**Límite conocido:** un modelo pequeño (en desarrollo, `qwen2.5:1.5b`) a veces no aplica bien el feedback. Para eso está el límite de iteraciones y `needs_human_review`.

## Pruebas

- **`tests/pipelines/test_rfp_response.py`** (16 tests; cada prueba del grafo ejecuta antes la Parte 1 sobre los fixtures de ModaViva y Luna):
  - **entrada:** los datos salen del handoff, no del PDF; sin handoff no se genera;
  - **generador:** `warehouse` (formato del BRIEFING, EUR, 15000 SKU, SLA, tabla de descuentos, nada inventado) y `lastmile` (USD, inglés, sin LLM);
  - **evaluadores:**
    - cumplimiento: el fixture `fixtures/rfp/draft-reverse-returns-24h.md` da `compliance.pass == false` con la regla `BRIEFING-5.3-returns-min-48h`; además se prueban las otras cuatro reglas y la ausencia de falsos positivos con «nunca en menos de 48 horas»;
    - pertinencia: cifras inventadas y servicio excluido;
    - legibilidad: oraciones larguísimas, con el feedback que las cita;
  - **separación y paralelismo:** un agente por departamento; los tres evaluadores pasan juntos una barrera de hilos (en serie se bloquearían); un departamento lento no bloquea a otro;
  - **ciclo:** falla, vuelve con feedback y pasa en la 2.ª iteración; o agota el límite y queda en `needs_human_review` dentro del handoff; forma del `EvaluationResult`; arranque solo desde `analisis_completo`.
- **`services/api/tests/test_rfp_intake_api.py`:** `POST /generate` devuelve 409 si la Parte 1 no terminó y 202 después; el detalle incluye borradores, evaluaciones e historial.
- **`uis/backoffice/src/__tests__/rfps.test.ts`:** sondeo durante la Parte 2, cuándo se puede regenerar y parseo del borrador.
- **Prueba real:** los tres PDF de muestra subidos por la UI; la evidencia está en `docs/pr-evidence/rfp-response/`.
