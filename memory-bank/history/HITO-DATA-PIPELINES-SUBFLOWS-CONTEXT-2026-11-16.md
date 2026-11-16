El pipeline básico está listo. Antes de la entrega final al equipo de operaciones, necesito cuatro cosas más:

El flow principal está creciendo — refactorízalo en subflows para que cada fase sea independiente, testeable, y reutilizable.
Necesito tests unitarios para las tasks de transformación. Si un test falla, quiero saberlo antes de que el pipeline llegue a producción, no después.
Tras el refactor a subflows, el pipeline debe seguir pudiendo correrse como script: python data/pipelines/pipeline.py completa sin errores (conserva el entrypoint de la Parte 2).
Necesito un dashboard. Nadie en el equipo de liderazgo va a consultar un endpoint — pon los KPIs en algún lugar donde realmente puedan mirarlos.
Punto de partida: data/pipelines/pipeline.py de la sesión anterior.

Mantén la estructura de carpetas existente: data/pipelines/ para flows y subflows, data/process/ para lógica de transformación, data/raw/ para datos de entrada, data/eval/ para salidas de validación.
Los tests unitarios van en tests/pipelines/ en la raíz del monorepo.
La página del dashboard va en uis/backoffice/, consumiendo el endpoint de consulta de KPIs de services/reporting/ que construiste en la Parte 2.
Asegúrate de que Prefect 3 esté instalado desde la Parte 2: uv add "prefect>=3".