# Evidencia: recepción de RFP subidas por la UI (Hito 9, Parte 1)

Ejecución real del 2026-11-22 con:
- el backoffice (`uis/backoffice`, página `/rfps`);
- el backend existente (`services/api`, rutas `/rfp-intake`);
- el worker de Celery con Redis;
- el LLM de desarrollo `qwen2.5:1.5b` (Ollama);
- **PostgreSQL en Supabase**.

Los tres PDF de muestra del BRIEFING se subieron **por el formulario de la UI** con Chromium headless (`scripts/rfp_ui_smoke.cjs`). Los PDF quedaron en `mock_data/raw/rfp/` (el `data/raw/` del BRIEFING; ignorado por git).

| PDF | Esperado (BRIEFING §4) | Resultado | Clasificación (reglas + agente) | Departamentos | País · moneda |
|---|---|---|---|---|---|
| `trackflow-request-1.pdf`: ModaViva (formal) | aceptar; `warehouse` + `reverse`; EUR | **analisis_completo** | `reglas_y_llm_coinciden` · agente `rfp_de_cliente` | warehouse, reverse (sin lastmile: transportista propio) | ES · EUR |
| `trackflow-request-2.pdf`: Luna Cosmetics (informal) | aceptar; `warehouse` + `lastmile`; USD | **analisis_completo** | `reglas_y_llm_coinciden` · agente `rfp_de_cliente` | warehouse, lastmile | US · USD |
| `trackflow-request-3.pdf`: RapidShip (pitch) | rechazar | **descartado** | `reglas_y_llm_coinciden` · agente `oferta_de_proveedor` | — | — |

| Archivo | Qué es |
|---|---|
| `01-rfps-antes-de-subir.png` | La página `/rfps` antes de la subida |
| `02-subidas-analizando.png` | Justo tras subir: «3 RFP subida(s): un ticket por documento, ya en análisis» |
| `03-estados-finales.png` | La UI tras refrescar sola: Descartado / Análisis completo / Análisis completo |
| `04-detalle-*.png` | Detalle de cada ticket: metadatos, legibilidad, clasificación, resumen para Ventas, secciones por departamento y preguntas abiertas |
| `supabase-rows.json` | Filas reales de `rfp_tickets`, `rfp_metadata` y `rfp_department_sections` de esta subida, incluido el contrato `rfp_handoff.v1` |

## Lo que muestran las filas

- **ModaViva:**
  - 15.000 SKU y fecha límite 2026-08-28.
  - Sin volumen mensual ni presupuesto: `warehouse` lo pregunta al cliente en lugar de inventarlo.
  - La «Integración con nuestro ERP vía API» queda como `out_of_scope_requests`, porque ningún departamento del BRIEFING la cubre.
  - `rfp_id` = la referencia del documento (`MV-LOG-2026-03`).
- **Luna Cosmetics:**
  - 5.000 pedidos/mes.
  - «around August 24th» se guarda como texto, sin inventar el año.
  - El worker de `warehouse` descartó un aspecto del LLM sin cita literal de la RFP (`worker_meta.dropped_unsupported`).
- **RapidShip:** el motivo del descarte es el de las reglas («Es un proveedor ofreciendo sus servicios o tarifas a TrackFlow…»). El razonamiento del agente queda en `classification.llm`.
- **Secciones:** cada una lleva el `approver` del BRIEFING (Ana Whitfield, Carlos Vega, Sofía Ramos) y `approval_status = pendiente` para la Parte 3.

## Primera pasada (conservada en la tabla)

En una primera subida, con la versión anterior del prompt, el agente devolvió `es_rfp: false` para los tres documentos. Sin embargo, su motivo decía «RFP para propuesta de servicios logísticos»: el esquema booleano confundía al modelo de 1,5 B.

La tabla de decisión aceptó igualmente las dos RFP válidas («las reglas aceptan, el LLM rechaza → aceptar + revisión de Ventas»), que es justo la protección contra falsos negativos. Después el agente pasó a un veredicto categórico y ahora coincide con las reglas en los tres casos.

Esos tickets siguen visibles en la tabla de la UI (las filas de las 9:06 y las 9:17).
