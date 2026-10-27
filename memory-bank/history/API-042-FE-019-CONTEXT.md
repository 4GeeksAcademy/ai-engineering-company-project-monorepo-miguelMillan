API-042 — Pruebas unitarias para los endpoints del backoffice
Prioridad: Baja

Contexto: La API del backoffice nunca ha tenido batería de pruebas. No se han reportado regresiones, pero probablemente es porque el equipo es pequeño, no porque el código sea sólido. Ahora que tenemos pytest configurado, ampliemos la cobertura antes de que el equipo crezca.

Alcance:

Elige al menos dos grupos de endpoints del backoffice distintos a la autenticación (p. ej., recursos, usuarios, elementos — lo que tenga el dominio de tu empresa)
Aplica la misma estructura de tres niveles: camino feliz, caso límite, modo de fallo
Apunta a un 60% de cobertura en los módulos que pruebes — el listón es más bajo que en auth, pero sigue siendo significativo
Entregable: Nuevos módulos de prueba añadidos al directorio tests/ existente. Actualiza TESTING.md con los nuevos resultados de cobertura.

Ticket: FE-019 — Pruebas unitarias para las funciones de utilidad del frontend
Prioridad: Baja

Contexto: El frontend ha ido acumulando funciones de utilidad a lo largo de los hitos anteriores — validadores de formularios, formateadores de datos, manejadores de respuestas de API — que nunca se han probado. Un bug en cualquiera de ellas podría romper la UI de forma silenciosa y difícil de rastrear.

Alcance:

Identifica al menos tres funciones de utilidad o helper en tu frontend Next.js / TypeScript
Escribe tests de Jest para cada una: una prueba de camino feliz y una de modo de fallo por función
Buenas candidatas: validadores de entrada, formateadores de fechas o monedas, parsers de respuestas, helpers de almacenamiento de tokens
Entregable: Un directorio __tests__/ dentro de tu proyecto frontend con los archivos de prueba. Actualiza TESTING.md con las instrucciones para ejecutar los tests del frontend de forma independiente.

(Extra) Escribe tests con pytest para al menos dos grupos de endpoints del backoffice, alcanzando un 60% de cobertura en esos módulos.
(Extra) Escribe tests de Jest para al menos tres funciones de utilidad del frontend (camino feliz + modo de fallo en cada una).
(Extra) Actualiza TESTING.md con los resultados de cobertura e instrucciones de ejecución para ambas suites adicionales.