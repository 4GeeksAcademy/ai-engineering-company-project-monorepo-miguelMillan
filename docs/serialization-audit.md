# Auditoría de serialización de FastAPI

**Fecha:** 2026-11-10  
**Alcance:** rutas registradas por `services/api/main.py` y los routers incluidos. Se documentan 30 operaciones de la aplicación, incluida la comprobación de salud. Las rutas `/api/incidents/analyze` y `/api/incidents/results/export` pertenecen al gateway Node, no a FastAPI; las rutas automáticas de documentación (`/docs`, `/redoc`, `/openapi.json`) tampoco son endpoints de negocio de esta auditoría.

## Criterios

- **Ya serializado:** declara un `response_model` adecuado o responde intencionalmente sin cuerpo con HTTP 204.
- **Parcialmente serializado:** declara un modelo, pero este incluye datos que el cliente no necesita, mezcla entidad y respuesta o devuelve una proyección demasiado amplia.
- **Sin serializar:** no declara un modelo de respuesta explícito. Un diccionario con forma estable también requiere un schema tipado.

La columna de respuesta refleja el contrato HTTP/OpenAPI observado. Las recomendaciones se basan en los campos consumidos por el backoffice cuando existe un consumidor en el repositorio.

## Inventario de Fase 1 (línea base)

| Método | Ruta | Propósito | Respuesta actual | Estado y mejora necesaria |
|---|---|---|---|---|
| GET | `/health` | Comprobar disponibilidad del servicio. | Dict `{status, service}`; sin `response_model`. | **Sin serializar.** Definir `HealthResponse` para documentar los dos campos estables. |
| POST | `/auth/forgot-password` | Solicitar un enlace de restablecimiento. | Dict `{message}` genérico; sin `response_model`. No devuelve el email. | **Sin serializar.** Añadir un schema de confirmación genérico; conservar el mensaje uniforme para evitar enumeración de cuentas. |
| POST | `/auth/reset-password` | Cambiar la contraseña mediante token de recuperación. | Dict `{message}`; sin `response_model`. No devuelve token ni contraseña. | **Sin serializar.** Añadir un schema de mensaje de éxito. |
| POST | `/auth/change-password` | Cambiar la contraseña de la sesión autenticada. | Dict `{message}`; sin `response_model`. | **Sin serializar.** Añadir el mismo tipo de schema de confirmación, sin incluir credenciales. |
| POST | `/auth/login` | Validar credenciales y emitir sesión. | `TokenResponse`: `access_token`, `token_type`, `expires_in`. | **Ya serializado.** Proyección adecuada para iniciar sesión; no reenvía el email ni la contraseña. |
| GET | `/auth/me` | Obtener identidad y perfil de la sesión. | `AuthMeResponse`: email, rol y `profile` anidado con `id`, `user_id`, nombre, teléfono y dirección. | **Parcialmente serializado.** La UI usa email y los campos de contacto, no los dos identificadores del perfil. Sustituir `Profile` anidado por una proyección segura sin IDs; conservar email porque se muestra en la cuenta. |
| POST | `/users` | Registrar una cuenta y su perfil. | `UserResponse`: id, email, estado, rol y fecha de creación; excluye contraseña y hash. | **Parcialmente serializado.** El frontend ignora el cuerpo y hace login a continuación; usar una respuesta mínima de registro, sin repetir el email salvo necesidad explícita del cliente. |
| GET | `/users` | Listar usuarios. | `list[UserResponse]`: id, email, estado, rol y fecha de creación. | **Parcialmente serializado.** No se encontró consumidor en el backoffice y la ruta solo exige una sesión, por lo que cualquier usuario autenticado puede obtener los emails del directorio. Reducir la proyección y restringir el acceso a roles autorizados; esta última es una observación de autorización, no solo de serialización. |
| GET | `/users/{user_id}` | Consultar un usuario propio o permitido. | `UserResponse`; excluye `hashed_password` y `user_uuid`. | **Ya serializado.** La proyección pública está separada del modelo de credenciales y la ruta aplica comprobación de propietario/admin. |
| PUT | `/users/{user_id}` | Actualizar email, contraseña o rol con controles de permisos. | `UserResponse`; no devuelve contraseña ni hash. | **Ya serializado.** Respuesta explícita y sin campos de credenciales; el cambio de rol está limitado a admin. |
| DELETE | `/users/{user_id}` | Eliminar usuario. | HTTP 204, sin cuerpo. | **Ya serializado.** Respuesta vacía intencional; no necesita serializer de cuerpo. |
| GET | `/profiles/me` | Leer el perfil propio. | `Profile`: id, user_id, nombre, teléfono y dirección. | **Parcialmente serializado.** La UI solo consume nombre, teléfono y dirección. Crear `ProfileResponse` con esos campos y omitir IDs internos. |
| PUT | `/profiles/me` | Actualizar el perfil propio. | `Profile`, con los mismos cinco campos. | **Parcialmente serializado.** La UI solo usa los tres campos editables; devolver una proyección sin `id` ni `user_id`. El request `ProfileUpdate` ya limita los campos admitidos. |
| POST | `/suppliers` | Registrar un proveedor. | `SupplierResponse` con id, datos comerciales, contacto, notas y fecha de actualización. | **Ya serializado.** El formulario usa la respuesta para confirmar el nombre; el schema coincide con el recurso que administra el directorio. |
| GET | `/suppliers` | Listar y filtrar proveedores por país/categoría. | `list[SupplierResponse]`. | **Ya serializado.** El directorio muestra y edita los campos de la proyección, incluidos contacto, notas, tarifas y estado. |
| GET | `/suppliers/{supplier_id}` | Consultar un proveedor. | `SupplierResponse`. | **Ya serializado.** Schema explícito y correspondiente al recurso del directorio. |
| PATCH | `/suppliers/{supplier_id}/rate` | Actualizar tarifa. | `SupplierResponse` completa. | **Ya serializado.** El formulario recibe el recurso actualizado y consume la tarifa y campos de directorio; el request solo admite la tarifa. |
| PATCH | `/suppliers/{supplier_id}/status` | Suspender o reactivar proveedor. | `SupplierResponse` completa. | **Ya serializado.** El formulario reemplaza la fila por el recurso actualizado; el request solo admite el estado. |
| DELETE | `/suppliers/{supplier_id}` | Eliminar proveedor. | HTTP 204, sin cuerpo. | **Ya serializado.** Respuesta vacía intencional. |
| POST | `/api/incidents` | Registrar incidencia. | `Incident` como `response_model`; incluye todos los campos de la entidad. | **Parcialmente serializado.** Usa el modelo de entidad/entrada como schema de respuesta y el formulario ignora el cuerpo. Definir `IncidentCreatedResponse` con identificador, estado y fechas; incluir el resto solo si un consumidor lo necesita. |
| GET | `/api/incidents` | Listar incidencias con filtros. | `list[Incident]`, incluidos título, descripción, categoría, estado, origen, sede, ID y fechas. | **Parcialmente serializado.** La descripción se muestra al expandir una fila, pero se descarga para todos los resultados. Definir una proyección de listado y cargar el detalle bajo demanda, o documentar por qué se mantiene la descripción en la lista. |
| GET | `/api/incidents/summary` | Obtener conteos por estado, categoría, origen y sede. | Dict con `total` y cuatro mapas de conteos; sin `response_model`. | **Sin serializar.** Definir un schema `IncidentSummaryResponse` que haga explícita la forma y los catálogos de cada mapa. |
| GET | `/api/incidents/{incident_id}` | Consultar el detalle de una incidencia. | `Incident`, con descripción y metadatos. | **Ya serializado.** Schema explícito apropiado para el detalle completo. |
| PATCH | `/api/incidents/{incident_id}/status` | Transicionar el estado de una incidencia. | `Incident` completa. | **Parcialmente serializado.** El cliente usa del resultado el id y estado para actualizar la fila; devolver una proyección como `{id, status, updated_at}` en vez de repetir título, descripción, origen y sede. |
| GET | `/inventory/products` | Listar SKU con stock calculado. | `list[SKUResponse]`: id, nombre, SKU, cliente, categoría, almacén y stock actual. | **Ya serializado.** Los campos coinciden con la tabla y los selectores de producto; el stock se calcula en el servidor. |
| POST | `/inventory/products` | Crear un SKU. | `SKUResponse`, incluido stock actual. | **Ya serializado.** Devuelve el recurso creado con el stock inicial calculado. |
| GET | `/inventory/products/{product_id}` | Consultar SKU y stock. | `SKUResponse`. | **Ya serializado.** Schema explícito adecuado para el detalle de inventario. |
| POST | `/inventory/orders/inbound` | Registrar una entrada de stock. | `StockEntryResponse`: movimiento, fecha y `user_uuid`. | **Parcialmente serializado.** El formulario no consume el cuerpo de creación y el UUID del operario es innecesario en esta respuesta. Preferir un acuse mínimo con id y datos del movimiento; conservar el UUID en el listado solo mientras lo requiera el historial. |
| POST | `/inventory/orders/outbound` | Registrar una salida o pérdida. | `StockExitResponse`: movimiento, tracking, fecha y `user_uuid`. | **Parcialmente serializado.** El formulario tampoco consume el cuerpo; omitir `user_uuid` en la respuesta de escritura y devolver un acuse mínimo con id y datos del movimiento. |
| GET | `/inventory/orders` | Listar movimientos de entrada y salida. | `list[StockOrderResponse]`, con producto anidado como `SKUResponse` completo y `user_uuid`. | **Parcialmente serializado.** El historial solo necesita el nombre del producto, id SKU y campos del movimiento; el stock actual, cliente y categoría anidados no se muestran. Aplanar a `product_name` y mantener `user_uuid`, que la UI sí presenta como dato de auditoría. |

## Resultado de Fase 2 (estado actual)

Se añadieron schemas de respuesta explícitos a todas las operaciones con cuerpo. Los dos DELETE declaran `response_model=None` y responden 204 sin cuerpo. Las 30 operaciones de la línea base quedan, por tanto, con un contrato explícito, sin devolver objetos ORM en crudo.

| Método y ruta | Schema y payload resultante | Decisión de relación / cambio de entrada |
|---|---|---|
| GET `/health` | `HealthResponse`: `status`, `service`. | No hay relación. |
| POST `/auth/forgot-password`, `/auth/reset-password`, `/auth/change-password` | `MessageResponse`: solo `message`. | No devuelve email, token ni contraseña; forgot conserva el mensaje genérico anti-enumeración. |
| GET `/auth/me` | `AuthMeResponse`: email, rol y perfil opcional con nombre, teléfono y dirección. | Mantiene el perfil anidado porque la vista consume sus campos; omite `id` y `user_id`. El email es el del propio llamante y la vista lo necesita. |
| POST `/users` | `UserRegistrationResponse`: id, estado, rol y fecha; sin email ni credenciales. | La UI hace login con los datos del formulario y no necesita repetir el email en la respuesta. Entrada `UserRegister` separada. |
| GET `/users` | `list[UserListItemResponse]`: id, estado, rol y fecha; sin email. | Solo admin puede consultar el listado. Entrada no aplica. |
| GET/PUT `/profiles/me` | `ProfileResponse`: nombre, teléfono y dirección. | Proyección plana del perfil propio; omite IDs internos. `ProfileUpdate` sigue siendo el schema de entrada independiente. |
| POST `/api/incidents` | `IncidentCreatedResponse`: id, estado, `created_at`, `updated_at`. | Respuesta de creación mínima; `IncidentCreate` no acepta estado y el servidor fija `open`. |
| GET `/api/incidents` | `list[IncidentListItemResponse]`: id, título, categoría, estado, origen y sede. | Proyección plana de listado; no incluye descripción ni fechas. El backoffice solicita `IncidentDetailResponse` al expandir una fila. |
| GET `/api/incidents/summary` | `IncidentSummaryResponse`: total y mapas tipados por estado, categoría, origen y sede. | No hay relación; se mantienen ceros para todos los valores permitidos. La ruta ya estaba protegida por dependencia a nivel de router. |
| GET `/api/incidents/{incident_id}` | `IncidentDetailResponse`: campos de listado, descripción y fechas. | Devuelve el detalle completo solo cuando se solicita por ID; no hay relación anidada. |
| PATCH `/api/incidents/{incident_id}/status` | `IncidentStatusResponse`: id, estado y fecha de actualización. | Proyección mínima para que la UI actualice la fila; `IncidentStatusUpdate` es el schema de entrada separado. |
| POST `/inventory/orders/inbound`, `/inventory/orders/outbound` | `StockMovementCreatedResponse`: solo id del movimiento. | No devuelve la FK `sku_id` ni el UUID del operario en el acuse; los schemas de request permanecen separados. |
| GET `/inventory/orders` | `list[StockOrderListItemResponse]`: campos del movimiento, `sku_id`, `product_name` y `user_uuid`. | SKU aplanado a `product_name`; se conserva `sku_id` porque se muestra explícitamente y `user_uuid` porque el historial lo presenta para auditoría. No incluye el objeto SKU ni su stock actual. |

`GET /inventory/products`, `GET /inventory/products/{product_id}` y las demás rutas ya adecuadas mantienen `SKUResponse`, `UserResponse` y `SupplierResponse` según corresponda. Las escrituras continúan usando schemas de entrada separados de sus respuestas.

## Hallazgos transversales

- Recuento de la línea base: **30 operaciones**, 14 ya serializadas, 11 parcialmente serializadas y 5 sin schema explícito. Tras Fase 2, todas las respuestas con cuerpo tienen `response_model`; los dos DELETE usan `response_model=None` y 204.
- **Credenciales:** ninguna respuesta incluye `password` o `hashed_password`. Login devuelve solo token y expiración; las respuestas de recuperación/restablecimiento/cambio son mensajes sin email. El registro tampoco repite el email. `/auth/me` conserva el email del propio llamante porque lo muestra el perfil.
- **Objetos ORM:** las respuestas SQLModel de inventario usan schemas que filtran los datos; entradas/salidas devuelven solo el ID creado. Los usuarios, perfiles, incidencias y proveedores se proyectan a schemas Pydantic explícitos. No se devuelve ningún objeto ORM en crudo.
- **Entrada de escrituras:** los modelos de request revisados prohíben campos extra; `user_uuid` se genera en servidor. `NewPasswordRequest` ya declaraba `extra="forbid"` en el código inspeccionado al iniciar Fase 2, así que la observación contraria de la línea base fue incorrecta y no requirió cambio. `IncidentCreate` ahora no acepta estado y el servidor fija `open`.
- **Autorización colindante:** las lecturas de inventario ahora requieren usuario autenticado y `GET /users` queda limitado a admin. `/api/incidents/summary` ya estaba protegido por la dependencia global de su router; la línea base la describió erróneamente como pública.

## Método y límite

La línea base se contrastó con los routers, schemas, OpenAPI generado y consumidores del backoffice. Fase 2 implementa los cambios y actualiza las proyecciones de cliente afectadas. No se cambió el contexto de negocio.

## Verificación de Fase 3

La suite existente completa pasó: `uv run --directory services/api pytest -q` (**66 passed**, una advertencia conocida de deprecación Starlette/httpx). OpenAPI confirma 30 operaciones; cada respuesta con cuerpo tiene un schema y los dos DELETE usan 204 sin cuerpo.

Se abrió `/docs` en el navegador del host y se observó HTTP 200 para `/docs` y `/openapi.json`. El entorno no dispone de navegador local ni de herramienta de control del navegador: el lanzador solo abre la página y no permite accionar `Try it out`. Por eso la interacción manual dentro de Swagger UI queda pendiente y no se presenta como completada. Como comprobación alternativa, se enviaron solicitudes HTTP a la instancia temporal y se compararon las claves exactas de respuesta con los componentes de OpenAPI en siete operaciones: `GET /health`, `POST /users`, `POST /auth/login`, `GET /auth/me`, `POST /api/incidents`, `GET /api/incidents` y `GET /api/incidents/{incident_id}`. Todas coincidieron; la lista no contiene `description` y el detalle sí.

Estado de contrato de las 30 operaciones:

| Método | Ruta | Schema de respuesta | Estado |
|---|---|---|---|
| GET | `/health` | `HealthResponse` | ✅ |
| POST | `/auth/forgot-password` | `MessageResponse` | ✅ |
| POST | `/auth/reset-password` | `MessageResponse` | ✅ |
| POST | `/auth/change-password` | `MessageResponse` | ✅ |
| POST | `/auth/login` | `TokenResponse` | ✅ |
| GET | `/auth/me` | `AuthMeResponse` | ✅ |
| POST | `/users` | `UserRegistrationResponse` | ✅ |
| GET | `/users` | `list[UserListItemResponse]` | ✅ |
| GET | `/users/{user_id}` | `UserResponse` | ✅ |
| PUT | `/users/{user_id}` | `UserResponse` | ✅ |
| DELETE | `/users/{user_id}` | Sin cuerpo (204, `response_model=None`) | ✅ |
| GET | `/profiles/me` | `ProfileResponse` | ✅ |
| PUT | `/profiles/me` | `ProfileResponse` | ✅ |
| POST | `/suppliers` | `SupplierResponse` | ✅ |
| GET | `/suppliers` | `list[SupplierResponse]` | ✅ |
| GET | `/suppliers/{supplier_id}` | `SupplierResponse` | ✅ |
| PATCH | `/suppliers/{supplier_id}/rate` | `SupplierResponse` | ✅ |
| PATCH | `/suppliers/{supplier_id}/status` | `SupplierResponse` | ✅ |
| DELETE | `/suppliers/{supplier_id}` | Sin cuerpo (204, `response_model=None`) | ✅ |
| POST | `/api/incidents` | `IncidentCreatedResponse` | ✅ |
| GET | `/api/incidents` | `list[IncidentListItemResponse]` | ✅ |
| GET | `/api/incidents/summary` | `IncidentSummaryResponse` | ✅ |
| GET | `/api/incidents/{incident_id}` | `IncidentDetailResponse` | ✅ |
| PATCH | `/api/incidents/{incident_id}/status` | `IncidentStatusResponse` | ✅ |
| GET | `/inventory/products` | `list[SKUResponse]` | ✅ |
| POST | `/inventory/products` | `SKUResponse` | ✅ |
| GET | `/inventory/products/{product_id}` | `SKUResponse` | ✅ |
| POST | `/inventory/orders/inbound` | `StockMovementCreatedResponse` | ✅ |
| POST | `/inventory/orders/outbound` | `StockMovementCreatedResponse` | ✅ |
| GET | `/inventory/orders` | `list[StockOrderListItemResponse]` | ✅ |