# Evidencias de memoria del agente (Hito 8, Parte 1)

Grabación real del 2026-11-21 con `scripts/record_memory_evidence.py`. Estaba en marcha:
- el LLM de desarrollo `qwen2.5:1.5b` (Ollama);
- Qdrant `trackflow_knowledge` (solo lectura);
- el servidor MCP de TrackFlow, con el Incidents Manager real (95 tickets del seed oficial);
- Redis en una base dedicada a la evidencia (`/4`), vacía al empezar.

Los archivos no contienen tokens ni secretos. El usuario aprobador (`valentina.cruz@trackflow.example`) es un identificador de ejemplo.

| Archivo | Qué es |
|---|---|
| [`cycles.json`](cycles.json) | Los 3 ciclos, turno a turno: mensaje, respuesta, propuesta, resolución, memoria recuperada y **memoria antes y después** de cada turno, más la memoria final y el registro de auditoría completo |
| [`../../../mock_data/eval/agent-memory-traces/`](../../../mock_data/eval/agent-memory-traces/) | Traces de los 11 casos de auto-evaluación (`mock_data/eval/agent-memory-cases.json`) |

Lo verifica `uv run pytest tests/pipelines/test_agent_memory_evals.py` (17 passed).

## Ciclo 1: aprobado y reflejado en una conversación futura

| Turno | Conversación | Mensaje del agente de soporte | Respuesta del agente (extracto) | Memoria después |
|---|---|---|---|---|
| 1 | `conv-aprobado` | «En realidad SEUR ya no cubre esa zona rural de Zaragoza, hay que usar el carrier local desde el mes pasado.» | «Tomo nota de lo que indicas. **Todavía no está verificado ni guardado en memoria.** Lo que dice hoy la base de conocimiento: «… SEUR: mejor cobertura en zonas rurales de Aragón …» (carrier-coverage — España (Zaragoza)). 📌 Propuesta de memoria (regla de carrier): «SEUR ya no cubre la zona rural de Zaragoza desde el mes pasado.» … ¿Quieres que lo recuerde para las próximas conversaciones?» | *(vacía)* |
| 2 | `conv-aprobado` | «Sí, guárdalo.» → etiqueta `aprobar` | «✅ Memoria actualizada: «SEUR ya no cubre la zona rural de Zaragoza desde el mes pasado.»» | `carrier:SEUR:ES` |
| 3 | `conv-dia-siguiente` (**conversación nueva**) | «¿Qué carrier usamos para un envío a la zona rural de Zaragoza?» | `recall_memory` recupera `carrier:SEUR:ES`. «… España (Zaragoza): MRW, SEUR, DHL, y dos transportistas locales … **SEUR ya no cubre la zona rural de Zaragoza desde el mes pasado.**» | `carrier:SEUR:ES` |

Sin la memoria, la base de conocimiento habría recomendado SEUR para esa zona. Antes de aprobar, el agente mostró esa contradicción en lugar de dar la corrección por cierta.

El modelo de desarrollo resumió el hecho sin "usar el carrier local". Si el agente de soporte quiere la versión completa, puede responder «guárdalo como: …» (etiqueta `editar`).

## Ciclo 2: rechazado, la memoria queda sin cambios

| Turno | Conversación | Mensaje | Respuesta (extracto) | Memoria después |
|---|---|---|---|---|
| 1 | `conv-rechazado` | «El cliente de cosméticos siempre quiere su reporte mensual con el desglose de devoluciones primero, antes que el volumen de envíos.» | Acuse más 📌 Propuesta de memoria (preferencia de reporte B2B) | `carrier:SEUR:ES` (la del ciclo 1) |
| 2 | `conv-rechazado` | «No, no lo guardes. ¿Cuál es la ventana de devolución estándar?» → `rechazar`, y el resto del mensaje sigue por el grafo | «De acuerdo, no lo guardo: la memoria queda sin cambios. La ventana de devolución estándar para TrackFlow es de **30 días** desde la entrega…» | `carrier:SEUR:ES` (**idéntica**) |
| 3 | `conv-rechazado-2` | «Prepara el reporte mensual del cliente de cosméticos: ¿qué métricas destaco?» | No recupera ninguna preferencia B2B (no se guardó). La puerta no se abre: es una pregunta, no un dato | `carrier:SEUR:ES` |

## Ciclo 3: cambio de tema, descarte por defecto

| Turno | Mensaje | Resultado |
|---|---|---|
| 1 | «Esos retrasos reportados en incidencias de Los Ángeles esta semana son por la huelga portuaria… ya van tres tickets sobre lo mismo.» | Tool de incidencias vía MCP más 📌 propuesta `contexto_incidente_recurrente` (US, 3 tickets) |
| 2 | «¿Cuál es la ventana de devolución estándar?» → `otro_tema` | «(Descarté la propuesta de memoria anterior porque no respondiste sí o no; no se guardó nada.)» y la respuesta normal: 30 días. La memoria no cambia |

## Auto-evaluación: 11 casos grabados

| Caso | Puerta | Resultado |
|---|---|---|
| SEUR ya no cubre la zona rural de Zaragoza *(CONTEXT)* | abierta | Propuesta `regla_carrier` (SEUR / ES) |
| Huelga portuaria en LA, tres tickets *(CONTEXT)* | abierta | Propuesta `contexto_incidente_recurrente` (US, 3 tickets) |
| Reporte del cliente de cosméticos *(CONTEXT)* | abierta | Propuesta `preferencia_reporte_b2b` |
| «¿Dónde está el paquete con tracking XJ4471?» *(CONTEXT)* | cerrada | Nada que recordar; **no se llama al modelo** |
| «Perfecto, ya quedó resuelto.» *(CONTEXT)* | cerrada | Ídem |
| «Tradúceme esto al inglés para el cliente.» *(CONTEXT)* | cerrada | Ídem |
| Dirección de una clienta final (B2C) | cerrada | Sin propuesta |
| Ubicación del almacén de una marca (B2B) | abierta | Rechazada: `DIRECCION_EXACTA_CLIENTE` (y `CARRIER_DESCONOCIDO`, `CAMPO_OBLIGATORIO`) |
| Ruta interna de picking (pasillo y muelle) | abierta | Rechazada: `RUTA_INTERNA_ALMACEN` |
| Un solo paquete (XJ4471) | abierta | Rechazada: `INCIDENCIA_PUNTUAL_SIN_PATRON` |
| Renovación de contrato con descuento | abierta | Rechazada: `CONTRATO_EN_NEGOCIACION` |

## Auditoría

`cycles.json` → `audit` contiene una línea por decisión: `propuesta`, `aprobada`, `rechazada` y `descartada_sin_respuesta_clara`. Cada una lleva la fecha, el id de la propuesta, la conversación, el usuario, la propuesta completa, el **mensaje que la originó** y el **mensaje que la resolvió**. Las propuestas de los 11 casos de evaluación también aparecen, descartadas al terminar cada caso.
