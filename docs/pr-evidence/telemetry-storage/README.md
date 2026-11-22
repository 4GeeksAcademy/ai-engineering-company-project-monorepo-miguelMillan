# Evidencia: almacenamiento de telemetría con eventos de negocio y lote mixto (fix del PR #24)

En la revisión del PR #24 se señaló que la evidencia anterior solo mostraba eventos técnicos (`api_request_completed`, `backoffice_section_viewed`, `frontend_web_vital_recorded`) y un lote sin rechazos (`received:3, stored:3, rejected:0`).

Esta evidencia es una ejecución real del 2026-11-22 contra **Supabase** (`public.telemetry_events`), usando el endpoint del PR #24 sin cambios (`POST /telemetry/events`) y el backoffice (`uis/backoffice`). Se generó con [`scripts/telemetry_storage_smoke.cjs`](../../../scripts/telemetry_storage_smoke.cjs) en Chromium headless.

## 1. Evento de negocio real: `inbound_order_created`

Se registró una entrada de stock desde **Inventario → Entrada de stock**: SKU `CLT-SNK-W-42-Z`, 3 unidades. Fue una acción real, no un evento escrito a mano: la orden 16 quedó en `stock_entry`.

El cliente de telemetría de la UI envió su lote de forma normal (flush cada 10 s) y el endpoint respondió **`{"received": 7, "stored": 7, "rejected": 0}`**. En Supabase quedó la fila del evento de negocio, con sus dimensiones en `tags`:

| Campo | Valor |
|---|---|
| `event_type` | `inbound_order_created` |
| `event_id` | `7b26936a-24d6-464e-9dea-eb4b6a932ea1` |
| `tags` | `warehouse: zaragoza`, `client_id: purestep_footwear`, `product_id: CLT-SNK-W-42-Z`, `product_category: fashion`, `quantity: 3`, `order_id: 16` |

## 2. Lote mixto con rechazos: `rejected > 0`

Se envió un solo lote de 5 eventos al mismo endpoint, desde la misma sesión del navegador. Respuesta: **`{"received": 5, "stored": 2, "rejected": 3}`**.

| # | Evento | Validación | ¿En Supabase? |
|---|---|---|---|
| 1 | `backoffice_section_viewed` | válido | sí |
| 2 | `frontend_retry_requested` | válido | sí |
| 3 | `inbound_order_created` con `warehouse: "LA"` | inválido: el almacén no está normalizado (JSON Schema) | no |
| 4 | `pedido_creado` | inválido: tipo fuera de la allowlist (Pydantic) | no |
| 5 | `backoffice_section_viewed` con `userId` = email | inválido: contiene PII en vez de `u_<sha256>` (Pydantic) | no |

Para comprobar la presencia en Supabase se consultó `telemetry_events` por `event_id`. Los 2 eventos válidos se guardaron aunque el lote traía 3 inválidos; los inválidos no se guardaron.

## Archivos

| Archivo | Qué es |
|---|---|
| `01-entrada-de-stock-registrada.png` | La UI tras registrar la entrada de stock que dispara el evento de negocio |
| `02-supabase-evento-de-negocio.png` | Respuesta al lote de la UI y filas guardadas en `telemetry_events`, con la fila `inbound_order_created` y sus `tags` |
| `03-lote-mixto-rejected.png` | Respuesta al lote mixto (`rejected: 3`) y presencia de cada evento en Supabase |
| `business-event-batch.json` | Lote real enviado por la UI y respuesta del endpoint. El `userId` (hash) está redactado. |
| `mixed-batch.json` | Lote mixto enviado y respuesta del endpoint |
| `supabase-check.json` | Resultado de la consulta a `telemetry_events` por `event_id` |

Las capturas 02 y 03 se renderizaron a partir de `business-event-batch.json`, `mixed-batch.json` y `supabase-check.json`: respuestas reales del endpoint y consulta real a Supabase.

## Prueba automática

[`services/api/tests/test_telemetry_events.py`](../../../services/api/tests/test_telemetry_events.py) cubre tres casos:
- el mismo lote mixto da `received 5 · stored 2 · rejected 3` e inserta solo `inbound_order_created` y `backoffice_section_viewed`;
- un lote solo con inválidos no escribe nada;
- un evento repetido no cuenta como guardado otra vez.
