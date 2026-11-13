El plan de telemetría está aprobado. Hoy implementas la mitad que el usuario nunca ve pero que hace posible todo lo demás: el sistema de captura en el frontend.

Cada evento de tu catálogo — obligatorio o identificado por ti — debe capturarse en el momento exacto en que ocurre en el backoffice, acumularse en una cola local, y enviarse al backend en lotes — nunca de a uno. Para verificar que los eventos llegan correctamente, también vas a crear un endpoint receptor mínimo en FastAPI: todavía no persiste nada, solo valida el formato y responde 200. La persistencia real en base de datos es trabajo de la Fase 3.

Tu tech lead te envió este mensaje:

"Quiero ver eventos fluyendo antes de construir la capa de almacenamiento. Crea el TelemetryService en el backoffice e instrumenta, como mínimo, todas las métricas obligatorias de TELEMETRY-PHASE1-CONTEXT-2026-11-11.md — y tantas del resto de tu catálogo como te alcance el día, priorizando cubrir distintas categorías (técnicas y de negocio) antes que profundizar en una sola.

Para verificar que el payload llega bien, agrega un endpoint stub en el backend — valida el formato y devuelve 200, sin escribir en base de datos todavía.

Una cosa importante: configura la URL del endpoint como variable de entorno desde el inicio. En la siguiente fase vamos a reemplazar el stub por la implementación real y el frontend no debería necesitar ningún cambio.

No quiero tracking disperso en cada componente. Todo pasa por una única función track() — nada más."