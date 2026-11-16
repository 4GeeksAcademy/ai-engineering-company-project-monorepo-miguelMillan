El diseño está aprobado. El liderazgo quiere ver este pipeline corriendo, no solo documentado. Requisitos no negociables antes de entregarlo a producción:

— El pipeline debe tolerar fallos parciales sin interrumpir toda la ejecución. — Las tasks que tocan servicios externos deben tener reintentos configurados. — El pipeline debe poder correrse como script desde la línea de comandos. — Si una task ya corrió exitosamente en la última hora, no debe repetirse innecesariamente.

Punto de partida: tu data/pipelines/PIPELINE_DESIGN.md del día anterior. Implementa lo que diseñaste — leyendo de telemetría, escribiendo en la nueva tabla de reporting de negocio que acotaste, sin que nada del reporte técnico existente cambie.

Escribe el código de tu pipeline en data/pipelines/. El punto de entrada principal debe llamarse data/pipelines/pipeline.py. Usa data/raw/ para datos de entrada y archivos intermedios, data/process/ para scripts de transformación reutilizables, y data/eval/ para salidas de validación del pipeline.

La task de extracción lee de telemetry_events (y cualquier otra tabla de dominio que necesites) — en modo solo lectura. La task de carga escribe en la tabla de destino nombrada en HITO-DATA-PIPELINES-CONTEXT-2026-11-15.md (bajo el esquema reporting) que diseñaste en la Parte 1. No escribas de vuelta en telemetry_events ni modifiques services/telemetry/analysis.py

Cualquier endpoint que exponga o dispare el pipeline (por ejemplo, para consultar el estado de la última corrida o lanzar una corrida manual) debe implementarse en services/reporting/, un módulo separado de services/telemetry/, importando funciones y flows desde data/pipelines/ según se necesite.
Instala Prefect 3 en tu entorno: uv add "prefect>=3".