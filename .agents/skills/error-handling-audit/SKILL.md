---
name: error-handling-audit
description: "Use when auditing a repository for error handling, resilience, unhandled async failures, raw error exposure, sensitive logs, frontend loading/error states, FastAPI exception handling, or Python script exit codes. Produces a read-only, severity-ranked audit report."
---

# Auditoría de gestión de errores

## Objetivo

Auditar el repositorio en busca de fallos de resiliencia y comunicación de errores. Entregar únicamente un informe con hallazgos verificables y correcciones sugeridas. La implementación de las correcciones corresponde al desarrollador.

Esta skill es estrictamente de solo lectura: no edites, crees, formatees ni elimines código, pruebas, configuración o documentación. No ejecutes comandos que escriban datos o alteren el estado del repositorio.

## Alcance del repositorio

Por defecto, analiza el repositorio abierto completo. Si el usuario indica una ruta o limita el alcance, respeta ese límite y decláralo en el informe. Primero identifica la estructura y los lenguajes reales; no des por hecho que todas las carpetas o tecnologías están presentes.

En este monorepo pueden existir, entre otras áreas:

- `uis/backoffice` y `uis/website`: interfaces TypeScript/React con Vite.
- `services/api`: API FastAPI/Python y gateway Node.js.
- `scripts`: herramientas y procesos Python.
- `packages`, `shared`, `src` y `workflows`: lógica compartida y automatizaciones.

Adapta la revisión a lo encontrado. Omite dependencias, artefactos generados, builds y cachés (por ejemplo `node_modules`, `.next`, `dist`, `build`, `__pycache__` y entornos virtuales). No leas ni reproduzcas secretos de `.env`, credenciales o archivos sensibles. Puedes identificar su ruta y describir el riesgo sin mostrar su contenido.

## Procedimiento de auditoría

1. Identifica los límites del repositorio y sus aplicaciones, servicios, scripts, clientes HTTP y capas comunes de manejo de errores.
2. Recorre los puntos de entrada y las operaciones de riesgo: `fetch` y clientes HTTP, `await`, lectura/escritura de archivos, parseo JSON/CSV, handlers de rutas, llamadas externas y logging.
3. Sigue las llamadas hasta sus wrappers, límites de framework y manejadores globales. No reportes como fallo una excepción gestionada correctamente en una capa común; explica el flujo que sustenta cada hallazgo.
4. Comprueba estados de carga, éxito y error en operaciones de UI, incluyendo limpieza en `finally`, valores nulos, mensajes y acciones disponibles para el usuario.
5. Examina scripts Python para validación de entradas, errores de archivos/CSV, mensajes en `stderr` y códigos de salida distintos de cero ante fallos críticos.
6. Asigna severidad por impacto y alcance. Usa rutas relativas al repositorio y números de línea precisos. Evita hallazgos duplicados y no presentes hipótesis como hechos.
7. No modifiques archivos ni ejecutes pruebas o comandos con efectos secundarios. Si una validación de runtime sería necesaria, indícala como limitación.

## Categorías obligatorias

Evalúa cada categoría y reporta solo problemas demostrables:

1. **TRY/CATCH AUSENTE**: operaciones asíncronas o de I/O sin una ruta explícita y adecuada de manejo de error. Considera el manejo centralizado existente.
2. **CATCH DEMASIADO AMPLIO**: `try/catch` o `try/except` que captura una región extensa o errores no relacionados, dificultando distinguir la causa o recuperarse correctamente.
3. **FALLOS SILENCIOSOS**: errores ignorados, como `catch` vacío o `except: pass`, sin registro seguro, recuperación ni señal al usuario.
4. **EXPOSICIÓN DE ERRORES EN CRUDO**: excepciones, tracebacks, mensajes técnicos o detalles internos que pueden llegar a la UI o a respuestas HTTP.
5. **FILTRACIÓN DE DATOS SENSIBLES**: logs o mensajes de error con credenciales, secretos, cadenas de conexión, rutas internas o datos personales. Nunca copies el valor sensible al informe.
6. **ESTADOS DE CARGA/ERROR AUSENTES EN LA UI**: operaciones que cargan datos sin representar correctamente carga, éxito y fallo; incluye estados que dejan la pantalla bloqueada o vacía.
7. **SIN LLAMADA A LA ACCIÓN PARA EL USUARIO**: errores visibles sin una salida razonable, como reintentar, volver a una vista segura o contactar soporte.
8. **SIN SYS.EXIT EN FALLO DE SCRIPT**: scripts Python que detectan un error crítico, lo comunican, pero terminan exitosamente o no propagan un código de error.

## Criterios por tecnología

### Frontend: TypeScript, React, Next.js u otros

- Localiza todas las llamadas `fetch`, clientes HTTP y operaciones asíncronas que cargan o mutan datos. Verifica manejo de rechazo en la llamada concreta o en un wrapper común que cubra esa ruta.
- Comprueba estados de carga, éxito y error; que el indicador de carga se limpie en `finally` o mecanismo equivalente, incluso al fallar o cancelar.
- Comprueba que errores técnicos se traduzcan a mensajes comprensibles y que los estados de error ofrezcan una acción útil.
- Revisa `?.`, valores por defecto y fallbacks al acceder a propiedades que puedan ser nulas o indefinidas.
- Comprueba que handlers y cancelación de peticiones no oculten errores relevantes ni actualicen componentes desmontados.

### Backend: Python/FastAPI y servicios Node.js

- Revisa cada handler y la frontera adecuada para capturar excepciones. Señala bloques amplios que convierten fallos inesperados en respuestas engañosas o que ocultan el contexto operativo.
- Verifica códigos HTTP apropiados (400, 401, 403, 404, 409, 422 o 500 según el contrato) y cuerpos JSON consistentes, sin traceback ni detalles internos.
- Revisa llamadas a APIs externas y servicios de terceros: timeout, errores de transporte/estado, respuesta inválida y manejo seguro de fallos.
- Comprueba que los logs de error aporten contexto operativo sin incluir secretos ni datos personales innecesarios.

### Scripts Python

- Revisa validación defensiva de argumentos, archivos, encabezados, filas y formatos antes del procesamiento.
- Comprueba manejo de errores de lectura/escritura y parseo CSV/JSON; los mensajes operativos deben ir a `stderr` y no revelar datos de filas o credenciales.
- Verifica que los fallos críticos terminan con código distinto de cero (`sys.exit(1)` o excepción no capturada equivalente) y que descartes recuperables se distinguen de fallos fatales.

### General

- Examina `console.error`, `console.log`, `print`, logging y respuestas HTTP para detectar filtraciones de información interna.
- No recomiendes eliminar logs útiles sin alternativa segura; prioriza sanitización, clasificación y mensajes adecuados por entorno.
- Mantén las sugerencias estrictamente dentro de resiliencia y comunicación de errores. No propongas funcionalidades nuevas ni refactors ajenos a un hallazgo.

## Severidad

Ordena los hallazgos de mayor a menor severidad:

- **CRÍTICO**: exposición confirmada de secretos/datos sensibles, pérdida de datos relevante o fallo de seguridad/servicio con impacto amplio.
- **ALTO**: fallo frecuente o de impacto importante que bloquea un flujo central, deja errores sin contener o expone detalles internos.
- **MEDIO**: fallo recuperable o localizado que degrada una operación o deja al usuario sin una recuperación clara.
- **BAJO**: inconsistencia de bajo impacto, principalmente operativa o de claridad, con afectación limitada.

No eleves la severidad por posibilidad hipotética. Indica el impacto observable y el alcance que justifican la clasificación.

## Formato de salida

Responde en el idioma del usuario. Entrega únicamente el informe de auditoría: sin prefacio conversacional, propuestas de implementación, cambios de código ni resumen promocional.

Para cada hallazgo usa este formato:

### [SEVERIDAD] [CATEGORÍA] — `ruta/relativa.ext:línea`
- **Problema:** una frase concreta sobre el comportamiento y su impacto.
- **Corrección sugerida:** una acción breve y acotada; no implementarla.

Agrupa los hallazgos por severidad, en este orden: CRÍTICO, ALTO, MEDIO y BAJO. Dentro de cada nivel, prioriza seguridad y flujos centrales. No incluyas una categoría sin hallazgos.

Al final añade una sección breve **Cobertura y límites** con las áreas revisadas, exclusiones relevantes y verificaciones no ejecutadas. Si no encuentras problemas demostrables, indícalo expresamente y no inventes hallazgos para completar la lista.