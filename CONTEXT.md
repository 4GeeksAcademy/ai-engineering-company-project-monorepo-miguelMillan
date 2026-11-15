"El stub ya cumplió su función — sé que los eventos llegan con el formato correcto. Ahora necesito que los guardes.

Crea la tabla en Supabase y reemplaza el stub por el endpoint real. El modelo Pydantic que definiste en la fase anterior es el contrato — úsalo para validar. Los eventos que no cumplan el contrato se rechazan individualmente, pero el resto del lote se persiste igual.

El frontend no toca nada. La URL del endpoint es la misma — solo cambia lo que pasa dentro del backend cuando llega el lote. Si el frontend necesita cambiar algo para que esto funcione, el diseño está mal."

Ten a mano tu docs/telemetry/event-schemas.json y telemetry-plan.md — confirma que el mapeo a tags preserva las dimensiones específicas de TELEMETRY-PHASE1-CONTEXT-2026-11-11.md (almacén, oficina, etc.) definidas en tu plan.

El frontend no se toca. Verifica que NEXT_PUBLIC_TELEMETRY_ENDPOINT siga apuntando al mismo endpoint — solo cambia lo que pasa dentro del backend.

Sigue el orden: tabla en Supabase → endpoint real → verificación end-to-end.