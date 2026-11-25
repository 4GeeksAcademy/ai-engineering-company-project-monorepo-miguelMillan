# Inventario y gobierno de los sistemas de IA de TrackFlow

Revisado el 25/11/2026. Fuente de responsables: `memory-bank/projectbrief.md` (§ departamentos) y `CONTEXT.md` §3. **Dueño de negocio** responde de qué puede hacer el sistema y acepta su riesgo. **Dueño técnico** responde de que los controles funcionen; en todos es Andrés Kim (CTO) con el equipo TrackFlow Tech. Falta la firma de cada persona: la asignación es una propuesta basada en su área (brecha B-1).

## 1. Componentes que pide CONTEXT.md §3

| Componente | ¿Existe? | Dueño de negocio | Riesgo principal | Estado del riesgo |
|---|---|---|---|---|
| Agente de soporte 24/7 (CX) | Sí: `services/agent/graph.py`, `services/api/routes/chat.py` | Valentina Cruz (CX) | Datos de otro pedido o cliente por prompt injection | Mitigado: permisos por sesión, guardas de entrada y salida |
| Motor de selección de carrier | **No.** Solo hay un directorio de proveedores, sin IA: `services/api/routes/suppliers.py` | Carlos Vega (Última milla) | Confirmar envíos sin validar | Sin superficie. Requisitos previos en el [informe](nist-report.md#5-acciones-irreversibles-contextmd-5) |
| RAG de políticas y SLA | Sí: `mock_data/pipelines/rag.py`, `mock_data/process/rag.py` | Valentina Cruz (CX); contenido: Sofía Ramos y Carlos Vega | Mezclar reglas de un cliente B2B con las de otro | Bajo hoy: ninguno de los 4 documentos contiene datos de una marca. Sin filtro por marca si se añadieran: brecha B-6 |
| Motor de aprobación de devoluciones | **No** | Sofía Ramos (Logística inversa) | Aprobar fuera de reglas | Sin superficie. Mismos requisitos previos |

## 2. Resto de sistemas de IA construidos

| Componente | Dónde | Qué decide o hace | Datos que toca | Dueño de negocio |
|---|---|---|---|---|
| Memoria del agente | `services/agent/memory.py`, `services/agent/memory_llm.py` | Propone recordar hechos; los confirma una persona | Reglas de carrier, incidentes, preferencias B2B | Valentina Cruz |
| Chat en tiempo real | `services/agent/chat/engine.py`, `services/agent/streaming.py` | Entrega respuestas por WebSocket | Conversaciones B2C y B2B | Valentina Cruz |
| Tools del agente (cliente MCP) | `services/agent/mcp_client.py` | 3 tools de solo lectura | Tickets, stock | Andrés Kim |
| Servidor MCP | `mcps/trackflow_mcp/server.py` | Expone tickets e inventario a agentes autorizados | Tickets, stock | Andrés Kim; datos: Valentina Cruz y Ana Whitfield |
| RFP: recepción (clasificador, orquestador, 3 workers) | `mock_data/pipelines/rfp_intake/graph.py`, `mock_data/pipelines/rfp_intake/agents.py`, `mock_data/pipelines/rfp_intake/llm.py` | Clasifica y extrae; descarta lo que no es RFP | Documentos comerciales de marcas | Miguel Torres (Comercial) |
| RFP: generación y evaluación | `mock_data/pipelines/rfp_intake/response_graph.py`, `mock_data/pipelines/rfp_intake/generation.py` y sus tres generadores | Redacta y evalúa borradores | Volúmenes y condiciones de marcas | Miguel Torres |
| RFP: aprobación y arbitraje | `mock_data/pipelines/rfp_intake/approval_graph.py`, `mock_data/pipelines/rfp_intake/checkpointing.py` | No decide: espera a cada responsable | Propuesta final | Ana Whitfield, Carlos Vega, Sofía Ramos (secciones); Miguel Torres (arbitraje) |
| Notificaciones RFP (SSE) | `services/api/routes/rfp_events.py` | Avisa; sin modelo | Cliente y servicios de la RFP | Miguel Torres |
| Tareas en segundo plano | `services/api/tasks.py` | Ejecuta los grafos de RFP | Los de cada grafo | Andrés Kim |
| Trazas de los agentes | `services/agent/tracing.py`, `services/agent/action_log.py`, `mock_data/pipelines/rfp_intake/tracing.py` | Registran qué hizo cada agente y por qué; sin modelo | Preguntas y respuestas | Andrés Kim |
| Predicción de ventas (ML clásico) | `scripts/train_sales_forecast.py`, `scripts/evaluate_sales_forecast.py` | Estima; sin LLM | Ventas agregadas | Miguel Torres |
| Herramientas de desarrollo con IA: `skills/`, `.agents/`, plantillas de `agents/` y `scripts/rfp_e2e.py` | Repositorio | Ayudan a programar y a demostrar; no se despliegan | Código | Andrés Kim |

## 3. Quién controla qué cuando lo provee un tercero

El proveedor responde de su servicio. **TrackFlow sigue siendo responsable del tratamiento** (RGPD art. 24 y 28; CCPA/CPRA como negocio frente a su *service provider*): de qué datos envía, con qué permisos y de avisar si hay una brecha. Para los datos de destinatarios que las marcas confían a TrackFlow, TrackFlow suele ser *encargado* y la marca, responsable: por eso también hay que avisar a la marca.

| Tercero | Qué aporta | Controla el proveedor | Controla TrackFlow (persona) | Datos que recibe |
|---|---|---|---|---|
| LLM de generación (API compatible con OpenAI; en desarrollo, Ollama `qwen2.5:1.5b` **local**) | Redacción y extracción | Modelo, disponibilidad, retención y uso para entrenar según contrato | Andrés Kim: proveedor, qué texto se envía, límites, parada de emergencia. **En producción aún no hay proveedor contratado (B-2)** | Pregunta saneada, fragmentos RAG, texto de la RFP |
| Embeddings (`fastembed`, modelo local; opcional por API) | Búsqueda semántica | Pesos publicados | Andrés Kim: versión fija y ejecución local | Ninguno sale si es local |
| Qdrant | Índice vectorial | Software | Andrés Kim: red, copia y reindexado | Políticas generales |
| Supabase (PostgreSQL) | Inventario, RFP, chats | Infraestructura, cifrado, copias según plan | Andrés Kim: accesos, RLS, región, contrato de encargado (B-3) | Pedidos, RFP, conversaciones |
| Redis | Cola y memoria | Software | Andrés Kim: red, persistencia | Memoria aprobada, tareas |
| Keycloak (OIDC) | Identidad de agentes | Software | Andrés Kim: clientes, scopes, rotación (B-4) | Credenciales de clientes |
| Resend | Correo de recuperación | Entrega | Andrés Kim: contenido y límite | Email del usuario |
| LangGraph, FastMCP, OpenAI SDK, MarkItDown | Librerías | Código | Andrés Kim: versiones fijadas y `pip-audit` | — (se ejecutan en casa) |
| Clientes MCP de terceros | Llaman a las tools | Su propio agente y lo que haga con la respuesta | Andrés Kim: scope por cliente; cerrar tickets exige una persona | Lo que permita su scope |
| Couriers (8) | Hoy **sin integración**: no hay credenciales en el sistema | — | Carlos Vega decidirá qué datos se comparten | Ninguno |

**Reglas para añadir un componente o un proveedor:** entra en este inventario con sus dos dueños antes de recibir datos reales; contrato de encargado o *service provider* firmado; modelo y versión fijos; ninguna acción de `CONTEXT.md` §5 sin pasar por `services/api/confirmations.py`; pruebas de `tests/security/` ampliadas.
