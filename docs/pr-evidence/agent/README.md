# Evidencias del PR del agente LangGraph (Parte 1)

Corrida real del endpoint `POST /agent/query` (2026-11-20), con Qdrant `trackflow_knowledge`, embeddings locales MiniLM y LLM de desarrollo Ollama `qwen2.5:1.5b`, sobre un usuario temporal en TestClient.

| Archivo | Qué es |
|---|---|
| [`inspect-runs.txt`](inspect-runs.txt) | `python -m services.agent.inspect`: las 3 corridas del endpoint (`generate`, `no_context`, `invalid_question`) |
| [`trace-01d087ec-returns-window.json`](trace-01d087ec-returns-window.json) | `python -m services.agent.inspect 01d087ec-…`: **export del trace completo** de "¿Cuál es la ventana de devolución estándar?" (nodos y decisiones de arista en orden, con su salida y duración) |
| [`checkpoints-01d087ec-returns-window.json`](checkpoints-01d087ec-returns-window.json) | Los 5 checkpoints de esa corrida (`get_state_history`): el estado crece de `question` a `+context` y a `+answer` |
| [`evals-output.txt`](evals-output.txt) | `uv run pytest tests/pipelines/test_agent_evals.py -v`: 10 passed, contra los traces de `mock_data/eval/agent-traces/` |
| [`full-test-suites.txt`](full-test-suites.txt) | Raíz (RAG, grafo y evals): 76 passed; `services/api`: 97 passed |
