# Evidencias del PR del agente con tools (Parte 2)

Ejecución real (2026-11-20) con el API de TrackFlow en marcha (`uvicorn main:app`, puerto 8081). Las incidencias provienen del seed oficial del CSV de la empresa, con 95 tickets en una TinyDB de desarrollo. El inventario se leyó de Postgres/Supabase (8 SKU). También estaban en marcha Qdrant `trackflow_knowledge` y el LLM de desarrollo Ollama `qwen2.5:1.5b`.

| Archivo | Qué es |
|---|---|
| [`api-access-log.txt`](api-access-log.txt) | Log de acceso del API. Las tools solo hicieron `GET /api/incidents/{id}`, `GET /api/incidents?status=…&category=…` y `GET /inventory/products`. **0 escrituras** (POST/PATCH/PUT/DELETE) a incidencias o inventario |
| [`inspect-runs.txt`](inspect-runs.txt) | `python -m services.agent.inspect`: corridas del endpoint con `fuentes=incidents`, `inventory` y `rag`, decididas automáticamente |
| [`trace-endpoint-ticket-status.json`](trace-endpoint-ticket-status.json) | Trace de `POST /agent/query` "¿En qué estado está el ticket d32a9b66-…?": `receive_question → plan_sources → lookup_incidents → compose_answer` |
| [`trace-endpoint-rag-policy.json`](trace-endpoint-rag-policy.json) | Trace de `POST /agent/query` "¿Cuál es la ventana de devolución estándar?": `receive_question → plan_sources → retrieve → generate`, solo RAG, sin tools |
| [`trace-mixed-ticket-and-policy.json`](trace-mixed-ticket-and-policy.json) | Trace de una pregunta mixta: `lookup_incidents → retrieve → generate → compose_answer`, con `sources_used = ["incidents", "rag"]` |
| [`evals-new-output.txt`](evals-new-output.txt) | Solo los evals nuevos de enrutamiento y fallback, más el caso RAG de control: 8 passed |
| [`evals-output.txt`](evals-output.txt) | `uv run pytest tests/pipelines/test_agent_evals.py -v`: 17 passed (12 casos y 5 meta-tests) |
| [`full-test-suites.txt`](full-test-suites.txt) | Raíz: 114 passed; `services/api`: 98 passed |

Las evidencias se revisaron: no contienen el JWT de la cuenta de servicio, `SECRET_KEY` ni contraseñas.
