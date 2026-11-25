# Respuesta a incidentes de IA y datos — TrackFlow

Para quien esté de guardia. Dueño: Andrés Kim (CTO). Decisión de notificar: Andrés Kim con Thomas (CEO) y asesoría legal. Este plan todavía no se ha ensayado (brecha B-7).

## Plazos

| Obligación | Plazo | Desde cuándo | A quién |
|---|---|---|---|
| RGPD art. 33 (`CONTEXT.md` §2) | **72 horas**; si falta información, por fases y justificando el retraso | Constancia de la brecha | AEPD, por su sede electrónica, salvo que el riesgo sea improbable |
| RGPD art. 34 | Sin dilación indebida, si el riesgo es alto | Constancia | Personas afectadas |
| RGPD art. 33.2 | Sin dilación indebida | Constancia | La marca responsable, cuando TrackFlow es encargado |
| California (`CONTEXT.md` §2: «razonablemente rápida») | Desde 2026, Cal. Civ. Code 1798.82 (SB 446) lo concreta en **30 días naturales**, con excepciones | Descubrimiento o aviso | Residentes de California afectados |
| California, más de 500 residentes | 15 días naturales (legal debe confirmar desde qué momento cuentan) | Según la ley | Fiscal General |
| Contratos B2B | Los del contrato; no están en el repositorio (B-3) | — | Marca afectada |

Ante la duda se usa el plazo más corto: **objetivo interno, decidir la notificación en 24 h**.

## Clasificación

| Nivel | Ejemplo | Primera respuesta |
|---|---|---|
| **S1** | Datos de un destinatario o de una marca vistos por otro; acción irreversible ejecutada sin confirmar; clave filtrada | 15 min, aviso a Andrés Kim y Valentina Cruz |
| **S2** | Inyección que llega al modelo sin fuga; memoria o documento envenenado; coste descontrolado | 1 h |
| **S3** | Intento bloqueado, repetido | Día laborable |

Estos tiempos son una propuesta: `CONTEXT.md` solo fija los plazos legales.

## Pasos

1. **Detectar.** Logger `trackflow.security` (`access_denied`, `ai_rate_limited`, `action_self_approval_denied`), `trackflow.agent.actions` y `GET /agent/guardrails/summary`.
2. **Contener (minutos).** Parar la IA sin reiniciar: `sudo trackflow-ctl ai-stop` (crea el archivo de `AI_KILL_SWITCH_FILE`); en local, `AI_KILL_SWITCH=1`. El agente responde que no está disponible; el resto del API sigue. Cuenta implicada: `accounts.py set-role <email> user` o cambio de contraseña, que corta sus sesiones. Agente externo: revocar su cliente en Keycloak.
3. **Conservar pruebas.** Antes de purgar nada: `GET /agent/runs/{run_id}/actions`, `GET /agent/memory/audit`, `GET /actions?status=all` y copia de `AGENT_DATA_DIR`.
4. **Evaluar.** Qué datos, de quién, de qué país, cuántas personas. Anotar la hora de constancia: ahí empieza el reloj.
5. **Notificar** según la tabla.
6. **Erradicar.** Documento RAG envenenado: corregir en el repo y reindexar (`uv run python -m mock_data.process.rag`). Memoria envenenada: B-8. Clave filtrada: rotar y revocar.
7. **Recuperar.** Quitar la parada de emergencia solo cuando pase `tests/security/` con un caso nuevo que reproduzca el incidente.
8. **Aprender.** Informe en 5 días laborables, con dueño y fecha por acción.

## Derechos de las personas

Supresión y acceso (RGPD art. 15 y 17; CCPA/CPRA: acuse en 10 días laborables, respuesta en 45 días naturales): cuenta con `DELETE /users/{id}` (confirmado), trazas del agente con `uv run python -m services.agent.purge --days N --confirm`. Los chats de Supabase y la exportación de datos no están automatizados (B-5).
