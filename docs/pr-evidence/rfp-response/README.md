# Evidencia: generación y evaluación de respuestas a RFP subidas por la UI (Hito 9, Parte 2)

Ejecución real del 2026-11-22 con:
- el backoffice (`/rfps`);
- el backend existente;
- el worker de Celery;
- el LLM de desarrollo `qwen2.5:1.5b` (Ollama);
- PostgreSQL en Supabase.

Los tres PDF de muestra se subieron por el formulario de la UI con `scripts/rfp_ui_smoke.cjs`. Al terminar la Parte 1, la Parte 2 arrancó sola (`rfp_response.generate_ticket`) a partir del handoff `rfp_handoff.v1`.

| PDF | Parte 1 | Parte 2 | Secciones |
|---|---|---|---|
| `trackflow-request-1.pdf`: ModaViva (ES) | `analisis_completo` | **`needs_human_review`** | `reverse`: superada en la 1.ª iteración. `warehouse`: **3/3 sin pasar**, porque el LLM escribió «asegurando un retorno a los clientes en menos de 48 horas» (`BRIEFING-5.3-returns-min-48h`) y no lo corrigió pese al feedback. Queda como borrador provisional con su `EvaluationResult` y pasa igual a la Parte 3. |
| `trackflow-request-2.pdf`: Luna Cosmetics (US) | `analisis_completo` | **`esperando_aprobación`** | `warehouse` y `lastmile` superadas en la 1.ª iteración, en USD y en inglés |
| `trackflow-request-3.pdf`: RapidShip (pitch) | `descartado` | no aplica | sin secciones |

Media de iteraciones por sección: (1 + 3 + 1 + 1) / 4 = 1,5. La meta del BRIEFING §3 es menos de 2.

| Archivo | Qué es |
|---|---|
| `01-rfps-antes-de-subir.png`, `02-subidas-analizando.png` | La página antes de la subida y justo después |
| `02b-en-evaluacion.png` | Un ticket en «En evaluación» mientras corre el ciclo generador-evaluador |
| `03-estados-finales.png` | Estados finales: Revisión humana / Esperando aprobación / Descartado |
| `04-detalle-trackflow-request-1.png` | ModaViva: aviso de revisión humana, borrador provisional de `warehouse` (iteración 3/3, la regla incumplida, la frase exacta, el feedback y el historial) y la sección `reverse` superada |
| `04-detalle-trackflow-request-2.png` | Luna Cosmetics: dos secciones superadas, en USD |
| `supabase-rows.json` | Filas reales de `rfp_tickets` (con `response_handoff` = `rfp_response_handoff.v1`), `rfp_department_sections` y `rfp_section_evaluations` (un `EvaluationResult` por iteración) |

**Limitación observada:** en la parte narrativa de `warehouse`, el modelo de 1,5 B escribió «costo estimado por pallet o SKU de 16 EUR al mes», cuando la tarifa es por m³. La cifra existe en el tarifario, así que ningún evaluador la marcó; la tabla de conceptos, que es determinista, sí dice «16 EUR por m³ al mes». Detectar la unidad equivocada exigiría un evaluador semántico, y la sección llega igualmente a su responsable (Ana Whitfield) en la Parte 3.

## Ejecución 2: agentes generadores separados por departamento

Tras la revisión se separó el generador en tres agentes (`generators/warehouse.py`, `lastmile.py` y `reverse.py`), cada uno con su propio alcance en el prompt. Después se regeneró el ticket de ModaViva con el LLM real, usando `scripts/rfp_intake.py generate`. Es la ejecución 2; la ejecución 1 se conserva en `rfp_section_evaluations` con `run = 1`.

| Sección | Resultado | Motivo |
|---|---|---|
| `warehouse` (`warehouse_generator`) | **Superada en la 1.ª iteración** (Fernández-Huerta 78,7) | Ya no habla de devoluciones y factura «por m³ ocupado, sin facturar por pallet ni por SKU». Los dos problemas de la ejecución 1 desaparecieron. |
| `reverse` (`reverse_generator`) | **`needs_human_review` (3/3)** | Legibilidad: una oración de 33 palabras (el máximo es 30). El feedback citaba la frase exacta y pedía dividirla, pero el modelo de 1,5 B la reescribió igual de larga. |

- `05-modaviva-run2-agentes-separados.png`: el detalle en la UI. Una sección provisional, con su aviso, la tarjeta «✗ Legibilidad · oración de 33 palabras (máximo 30)» y el historial de 3 iteraciones; la otra, superada.
- `modaviva-run2-separated-generators.json`: las evaluaciones de la ejecución 2 (el `EvaluationResult` y el borrador de cada iteración).

**Limitación:** el borrador de `warehouse` que pasó dice «lista para operar en menos de 30 días». El 30 existe en el tarifario (días de gracia), así que el control de cifras no lo marca, pero aquí se usa como plazo de onboarding, que el tarifario no fija. Lo revisa Ana Whitfield al aprobar en la Parte 3.
