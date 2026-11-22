# Evidencia: aprobación humana por departamento, arbitraje y documento final (Hito 9, Parte 3)

Ejecuciones reales del 2026-11-22 con:
- el backend existente y el worker de Celery;
- Supabase (PostgreSQL) como base de datos y **checkpointer** (`PostgresSaver`, hilos `rfp-{ticket_id}:approval-{run}`);
- el LLM de desarrollo `qwen2.5:1.5b` (Ollama).

## 1. E2E reproducible con el LLM real: `scripts/rfp_e2e.py`

Se usó el PDF `trackflow-request-2.pdf` (Luna Cosmetics, EE. UU.) con el escenario `capacity-conflict`. El informe completo está en `e2e-real-llm-luna.json`: línea de tiempo, 52 pasos de traza y documento final.

```
subida                               analizando
Parte 1: recepción                   analisis_completo       (clasificación: reglas_y_llm_coinciden)
Parte 2: generación y evaluación     esperando_aprobación    (warehouse y lastmile: fallan la 1.ª iteración, pasan la 2.ª)
Parte 3: aprobación iniciada         esperando_aprobación    lastmile y warehouse: esperando_aprobación
lastmile: aprobada por Carlos Vega   esperando_aprobación    lastmile aprobada · warehouse sigue esperando
warehouse: aprobada por Ana Whitfield (capacidad 4000/mes < 5000 de la RFP)
                                     esperando_aprobación    arbitraje volume-vs-capacity (árbitro Miguel Torres) → lastmile revisa
lastmile: aprobada tras arbitraje    terminado               documento final en USD, 2 secciones
```

## 2. El mismo flujo por la UI: `scripts/rfp_ui_approval.cjs`

| Captura | Qué muestra |
|---|---|
| `01-esperando-aprobacion.png` | Las Partes 1 y 2 terminaron solas y cada departamento espera a su responsable |
| `02-una-aprobada-otra-esperando.png` | **Carlos Vega aprobó `lastmile` mientras `warehouse` sigue esperando a Ana Whitfield** (1/2 aprobadas) |
| `03-arbitraje-nueva-version.png` | Ana Whitfield comprometió 4000 pedidos/mes: arbitraje `volume-vs-capacity` (Miguel Torres) y versión 2 de `lastmile` esperando a Carlos Vega |
| `04-terminado-documento-final.png` | Ticket `Terminado` con el documento final (descargable en `.md`) |
| `05-trazabilidad.png` | Qué agente hizo qué y en qué orden, de la recepción al documento final |

**Nota honesta sobre esta ejecución.** El Codespace se quedó sin memoria a mitad de la prueba: Ollama devolvió «model runner has unexpectedly stopped» y Chromium se colgó. El flujo se degradó como está diseñado:
- la clasificación quedó en «solo reglas»;
- los generadores escribieron sin LLM (marcado en `generator_meta.llm_error`);
- el script de la UI se retomó desde el punto en que estaba (`SKIP_UPLOAD=1`), sin reiniciar el flujo, porque el estado seguía en el checkpoint.

Esa ejecución destapó una inconsistencia entre partes: sin LLM, la narrativa en inglés copiaba los aspectos clave en español de la Parte 1. Quedó corregida: en inglés se usan los metadatos estructurados. La ejecución 1 de esta página, con el LLM funcionando, es la referencia.

## Correcciones tras revisar el documento final real

- En la versión en inglés, la fila de transportistas salía en español («UPS, FedEx y DHL»). Ahora hay un tarifario de transportistas por idioma.
- Tras el arbitraje `volume-vs-capacity`, la cabecera del documento no indicaba el volumen limitado. Ahora muestra «Proposal volume: 4000 orders/month (capped by arbitration; the RFP asked for 5000)».

`e2e-real-llm-luna.json` se regeneró después de estas correcciones.

## Limitaciones del modelo de desarrollo (1,5 B) que quedan visibles

- Las preguntas abiertas de la Parte 1 se generan en español aunque la propuesta sea en inglés. Ventas las revisa antes de enviar.
- El LLM a veces escribe la parte narrativa en otro idioma o repite parte de sus instrucciones. Ningún evaluador lo detecta todavía (no hay chequeo de idioma). El responsable lo ve al aprobar y puede editar la sección o pedir cambios.
