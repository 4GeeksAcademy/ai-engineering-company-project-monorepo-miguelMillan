Esta es la Parte 2 de 2 y depende directamente de la Parte 1: necesitas el grafo de LangGraph ya compilado, con tracing y evals funcionando, antes de continuar aquí.

Tu agente hoy solo sabe una cosa: buscar en la base de conocimiento del RAG. Pero soporte no vive solo de documentación — vive de datos que cambian todo el tiempo. El brief de tu tech lead es directo:

De: Tech Lead Para: Equipo de Agentes

El RAG que migraron en la Parte 1 responde bien preguntas sobre procedimientos y políticas, pero cuando un agente de soporte pregunta "¿en qué estado está el ticket 482?", el agente inventa una respuesta porque no tiene forma de consultar eso — esa información no vive en la base de conocimiento, vive en el gestor de incidentes que ya construimos.

Necesito que le den al agente una tool para consultar el sistema de incidentes en tiempo real. Como extra, si les alcanza el tiempo, otra tool para consultar el gestor de inventario también resuelve preguntas frecuentes de soporte ("¿tenemos stock de X?").

Criterio de aceptación: el agente debe decidir por sí mismo cuándo la pregunta requiere el RAG y cuándo requiere una tool externa — sin que el usuario tenga que especificarlo.

Dos requisitos quedan implícitos en ese brief y son fáciles de pasar por alto: (1) las tools deben leer de los servicios que ya construiste en proyectos anteriores (el gestor de incidentes y, si lo hiciste, el gestor de inventario) — no se simulan datos nuevos ni se inventa un dataset paralelo; y (2) si una tool falla o el servicio no responde, el grafo necesita una ruta explícita de recuperación, no un error que rompa toda la corrida.

Conocimiento complementario: por qué esto no es "otro RAG"
Es tentador resolver esto indexando los tickets en el mismo vector store del RAG. No lo hagas: los tickets cambian de estado en tiempo real, y el RAG está pensado para conocimiento relativamente estable (políticas, procedimientos). Una tool que llama directamente al endpoint del gestor de incidentes siempre te da el dato actual; una copia indexada del ticket queda desactualizada en el momento en que alguien cambia su estado. Esta es la diferencia entre "conocimiento" (RAG) y "datos operativos en vivo" (tool call).

Confirma que tu servicio del gestor de incidentes (GET /api/incidents, GET /api/incidents/{id}) está corriendo localmente — es el que construiste en un proyecto anterior del monorepo. Si construiste también el gestor de inventario (GET /inventory/products), tenlo disponible también para el extra.

No necesitas instalar dependencias nuevas más allá de las que ya usas para llamar HTTP desde tu backend