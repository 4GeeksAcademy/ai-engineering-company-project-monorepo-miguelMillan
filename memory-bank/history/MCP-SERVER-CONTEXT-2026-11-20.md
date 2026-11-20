Sobre los MCP Servers

Un MCP Server expone las capacidades de un sistema (herramientas, recursos, prompts) mediante un protocolo estándar que cualquier agente compatible puede descubrir y consumir, sin acoplarse al código interno de tu backend. A diferencia de las tools que ya conectaste directamente al grafo de tu agente, un MCP Server puede ser reutilizado por múltiples clientes — otros agentes, otros equipos, otras compañías del ecosistema — siempre que se autentiquen correctamente. Por eso la autenticación y el principio de mínimo privilegio no son un detalle: un servidor MCP sin auth es una vulnerabilidad real desde el primer día.

Tu agente ya sabe llamar herramientas directamente. Ahora tu tech lead ha abierto un ticket pidiendo que esas capacidades dejen de estar hardcodeadas dentro del grafo y se expongan como un servicio independiente, reutilizable y protegido por OAuth — y que el propio agente deje de llamar al Incidents Manager de forma directa para consumirlo a través del servidor MCP.

De: Tu tech lead Para: Tu squad Asunto: RFP — Servidor MCP para herramientas de la compañía

El agente que construimos ya consulta el Incidents Manager desde dentro del grafo, pero cualquier integración futura (otro agente, otro equipo, un partner externo) tendría que reimplementar esas mismas llamadas. Necesitamos exponerlas como un MCP Server independiente, autenticado con OAuth, para que cualquier cliente MCP autorizado pueda:

Gestionar tickets del Incidents Manager (crear, actualizar, consultar estado).
Consultar —nunca editar— los datos del inventario.
El servidor no debe dar más permisos de los estrictamente necesarios para cada tool. Documenten bien el discovery: cualquier cliente debe poder entender qué puede hacer el servidor sin necesitar contexto humano adicional.

Y no dejen la migración a medias: quiero que el propio agente reemplace su tool directa del Incidents Manager por una llamada al MCP Server como cliente. Si el agente sigue llamando al Incidents Manager por fuera del servidor, el ticket no está resuelto.

Los criterios de aceptación están en el checklist. Avísenme cuando esté listo para probarlo desde un cliente MCP.

Como parte del reto, tu implementación debe resolver — sin que se te diga explícitamente en un checklist — las siguientes decisiones de diseño:

Qué transporte usar (stdio vs. Streamable HTTP) según si el servidor se consume localmente o por múltiples clientes remotos, y qué implica esa elección para la autenticación.
Cómo estructurar el sistema de permisos para que la tool de inventario sea, por diseño, de solo lectura — no basta con "no implementar" el endpoint de escritura, el servidor debe rechazar explícitamente cualquier intento.
Qué información exponer en el discovery (nombres, descripciones y esquemas de las tools) para que un agente externo, sin contexto humano previo, entienda qué puede y qué no puede hacer.
Cómo reemplazar, dentro del grafo del agente, el nodo que llamaba directamente al Incidents Manager por tools del MCP Server vía langchain-mcp-adapters — sin romper el enrutamiento entre RAG y tools que ya tenías funcionando.


Trabaja sobre el backend del Incidents Manager y del módulo de inventario que ya construiste en hitos anteriores — el MCP Server se apoya en esos servicios, no los reemplaza.
Instala las dependencias necesarias con uv add (por ejemplo, fastmcp, mcpauth, langchain-mcp-adapters) — nunca uses pip install directamente en este monorepo.
Crea el servidor MCP dentro de la carpeta mcps/ del monorepo (no bajo services/).
Cablea OAuth con MCP Auth (paquete Python mcpauth) — OAuth 2.1 / OIDC listo para usar en servidores MCP como resource server. No uses las helpers de auth integradas de FastMCP; usa MCP Auth para Protected Resource Metadata, validación de JWT bearer y scopes.
Ubica el nodo del agente que hoy llama directamente al Incidents Manager — es el punto que vas a migrar para que consuma el nuevo MCP Server como cliente en lugar de llamar la API por fuera de él.