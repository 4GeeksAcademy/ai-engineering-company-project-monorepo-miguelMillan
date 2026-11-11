# Informe de caching

Fecha: 2026-11-11. Alcance: FastAPI y backoffice React/Vite de TrackFlow. El proyecto no utiliza Next.js; no se ha migrado de framework.

## Medición y metodología

`services/api/main.py` registra método, path, estado y milisegundos con `uvicorn.error.timing`, sin query string ni cabeceras. No hay telemetría de tráfico de producción disponible: la frecuencia de la tabla es una hipótesis basada en los flujos existentes, no una medida real. Los tiempos tampoco demuestran que los endpoints superen 100 ms en producción.

`scripts/benchmark_caching.py` crea exclusivamente bases TinyDB y SQLite temporales, una cuenta de prueba y 3000 incidencias sintéticas con categorías, estados, orígenes, sedes, fechas y textos variados. No modifica la base configurada del usuario. Compara 0 y 3000 registros, con diez GET autenticados consecutivos por ruta; las cifras son medianas de una ejecución local con `TestClient`, no de red ni de producción. En el caso cacheado la primera petición es fría y las siguientes son candidatas a acierto. La inserción directa del seed limpia la caché antes de medir.

| Ruta | Filas | HTTP sin caché | HTTP con caché | Middleware sin / con caché |
|---|---:|---:|---:|---:|
| GET `/api/incidents` | 0 | 2,18 ms | 2,26 ms | 1,29 / 1,34 ms |
| GET `/api/incidents/summary` | 0 | 2,07 ms | 2,05 ms | 1,19 / 1,12 ms |
| GET `/api/incidents` | 3000 | 47,32 ms | 11,30 ms | 44,96 / 10,06 ms |
| GET `/api/incidents/summary` | 3000 | 39,53 ms | 8,31 ms | 37,49 / 7,33 ms |

La latencia restante incluye autenticación y serialización. Los resultados son sensibles a carga y entorno; no se extrapolan a producción. Para reproducir, desde la raíz:

```bash
uv run --directory services/api python ../../scripts/benchmark_caching.py
uv run --directory services/api python -c 'import runpy; import routes.incidents as routes; routes.cached_incidents = lambda *args: None; routes.store_incidents = lambda *args: None; runpy.run_path("../../scripts/benchmark_caching.py", run_name="__main__")'
```

El segundo comando desactiva temporalmente los aciertos y almacenamientos solo dentro de ese proceso, sin modificar archivos. Para priorizar con evidencia real faltan frecuencias, distribución de filtros, tamaño de datos y latencias de producción.

## Frontend

- `IncidentAnalysis`: diferido hasta seleccionar análisis CSV; no hace falta en la vista inicial. Chunk de producción: 7,95 kB, 2,72 kB gzip.
- `SupplierDirectory`: diferido hasta seleccionar proveedores; incluye formulario y directorio. Chunk: 14,51 kB, 4,30 kB gzip.
- `InventoryPage`: diferido hasta entrar en una ruta de inventario. Chunk: 16,66 kB, 4,47 kB gzip.

`React.lazy` y `Suspense` muestran un estado de carga accesible. Los tamaños son los emitidos por Vite, no una medición de descarga ni una comparación antes/después; módulos compartidos pueden cargarse por separado. Faltan Network y comprobación visual en navegador.

`IncidentAnalysis` memoiza, con `[report]`, la traducción y concatenación de motivos de todas las filas inválidas. Con R filas y M motivos en total evita repetir O(R + M) operaciones al cambiar estados como arrastre o descarga; se recalcula al cambiar el informe. El ahorro es estimado, no medido con React Profiler. No se memoizan los contadores pequeños de reglas: su coste es trivial.

## Backend: decisiones

Ambas rutas cacheadas requieren autenticación en cada petición. La clave incluye el ID del usuario autenticado; el listado añade estado, origen, sede y categoría, incluidos valores ausentes. No se cachean tokens ni respuestas de error. Máximo 32 entradas por aplicación, expulsión de la más antigua y TTL de 5 segundos medido con reloj monotónico; las entradas caducadas se reemplazan al consultarlas. La creación y el cambio de estado de incidencias limpian todas las entradas bajo el mismo bloqueo usado para leer y generar resultados, evitando que una lectura concurrente reinstale resultados anteriores a una escritura local.

| Endpoint | Coste y frecuencia estimada | TTL e invalidación |
|---|---|---|
| GET `/api/incidents` | O(N) lectura y filtrado de TinyDB, validación y proyección. Alta al abrir/actualizar el directorio y cambiar filtros; repetición exacta no medida. | 5 s; invalidación en POST de incidencia y PATCH de estado. |
| GET `/api/incidents/summary` | O(N) lectura, validación y cuatro agregaciones. Alta al abrir/actualizar el directorio y tras cambios de estado; repetición no medida. | 5 s; mismas escrituras invalidan el resumen. |

### Frescura y límites

Cinco segundos equilibran ráfagas de lectura con la necesidad operativa de ver incidencias recientes. Las escrituras realizadas por esa instancia se reflejan inmediatamente al invalidar; cambios externos o de otro proceso pueden quedar desactualizados hasta cinco segundos. Esto solo es aceptable para consultas informativas de este directorio, no para tomar decisiones que exijan consistencia inmediata. La caché y el bloqueo son locales a cada proceso: no coordinan workers ni escrituras por scripts; TinyDB tampoco es una solución de escritura multiproceso. Para despliegues con varios workers o integración externa se necesita almacenamiento transaccional e invalidación compartida o eliminar esta caché. El límite de 32 entradas restringe el número, no los bytes: listados grandes por usuario pueden consumir memoria significativa.

No se cachea `/inventory/products`: el stock requiere frescura operacional y puede cambiar por entradas y salidas; tampoco se cachean perfil, sesión, usuarios, credenciales ni detalles de incidencias. Las operaciones de escritura y el análisis CSV deben ejecutarse cada vez.

## Inventario de endpoints FastAPI

Frecuencia: baja (acción puntual), media (navegación o edición), alta (lecturas repetidas de vistas); estimaciones cualitativas sin tráfico real. Estabilidad: cuando cambia la fuente o, en escrituras, cada operación. Coste: relativo al trabajo efectuado, no una latencia medida salvo la tabla anterior. Rutas automáticas de documentación y gateway Node quedan fuera del inventario de FastAPI.

| Método y ruta | Coste | Frecuencia estimada | Cambio de datos / estabilidad |
|---|---|---|---|
| GET `/health` | Mínimo, sin consulta | Alta por monitorización | Respuesta fija; no precisa caché |
| POST `/auth/login` | Hash de contraseña y lectura de usuario | Media | Sesión/token nuevo cada llamada |
| POST `/auth/forgot-password` | Búsqueda y emisión de recuperación | Baja | Token y solicitud nuevos |
| POST `/auth/reset-password` | Verificación de token y hash | Baja | Contraseña y token cambian |
| POST `/auth/change-password` | Verificación y hash | Baja | Contraseña cambia |
| GET `/auth/me` | Consulta y validación de sesión/perfil | Alta al cargar vistas privadas | Personalizado; cambia con cuenta/perfil |
| POST `/users` | Hash y escritura de usuario/perfil | Baja | Crea cuenta |
| GET `/users` | Lectura y proyección de usuarios | Baja, solo admin | Cambia con altas/ediciones/bajas |
| GET `/users/{user_id}` | Consulta de usuario y permisos | Baja | Datos personales pueden cambiar |
| PUT `/users/{user_id}` | Validación, posibles hashes y escritura | Baja | Cambia usuario/credenciales |
| DELETE `/users/{user_id}` | Permisos y borrado | Baja | Elimina usuario |
| GET `/profiles/me` | Consulta de perfil | Media | Personalizado; cambia al editar |
| PUT `/profiles/me` | Validación y escritura | Baja | Cambia perfil |
| POST `/suppliers` | Validación y escritura | Baja | Crea proveedor |
| GET `/suppliers` | O(N) lectura, filtro y serialización | Media al abrir/filtrar directorio | Cambia en altas, tarifas, estados y bajas |
| GET `/suppliers/{supplier_id}` | Consulta y serialización | Baja | Cambia al editar o eliminar |
| PATCH `/suppliers/{supplier_id}/rate` | Consulta y escritura | Baja | Cambia tarifa |
| PATCH `/suppliers/{supplier_id}/status` | Consulta y escritura | Baja | Cambia estado |
| DELETE `/suppliers/{supplier_id}` | Consulta y borrado | Baja | Elimina proveedor |
| POST `/api/incidents` | Validación y escritura | Media | Crea incidencia; invalida caché |
| GET `/api/incidents` | O(N), filtro y validación | Alta estimada | Cambia en creación y transiciones; cacheado |
| GET `/api/incidents/summary` | O(N), cuatro agregaciones | Alta estimada | Cambia en creación y transiciones; cacheado |
| GET `/api/incidents/{incident_id}` | Búsqueda y validación | Media al expandir | Puede cambiar inmediatamente |
| PATCH `/api/incidents/{incident_id}/status` | Búsqueda, transición y escritura | Media | Cambia estado; invalida caché |
| GET `/inventory/products` | Lectura y dos agregaciones por SKU | Alta en productos/selectores | Stock cambia con movimientos; sin caché |
| POST `/inventory/products` | Escritura y cálculo de stock | Baja | Crea SKU |
| GET `/inventory/products/{product_id}` | Lectura y dos agregaciones | Media | Stock cambia con movimientos |
| POST `/inventory/orders/inbound` | Comprobación y escritura | Media | Incrementa stock |
| POST `/inventory/orders/outbound` | Bloqueo, cálculo y escritura | Media | Reduce stock |
| GET `/inventory/orders` | Lecturas, consultas de productos y ordenación | Media al abrir historial | Cambia con movimientos; incluye UUID de operario |

## Verificación y pendientes

- Pruebas existentes `tests/test_incidents.py`: 4 correctas. Comprobación temporal adicional: autenticación, aislamiento por usuario y filtros, expiración simulada e invalidación en POST/PATCH correctos.
- `npm --prefix uis/backoffice run build`: correcto; lint correcto con advertencia preexistente `react(set-state-in-effect)` en `AccountProfile`.
- Sin prueba visual, Network ni React Profiler: el entorno no dispone de navegador automatizable funcional. No se afirma una mejora medida de render ni de experiencia de usuario.
- Pendientes: medir tráfico y latencias reales, validar con navegador, dimensionar memoria de caché y revisar coherencia multiproceso antes de producción. PageSpeed Insights no se verificó: no se dispone de una URL pública accesible para esta versión.