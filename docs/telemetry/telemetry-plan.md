# Plan de telemetría de TrackFlow: fase 1

## Alcance y fuentes

Catálogo y contrato propuestos, no instrumentación implementada. La fuente normativa es [CONTEXT.md](../../CONTEXT.md); se contrastó con [el briefing anterior de inventario](../../memory-bank/history/HITO05-CONTEXT.md), [el router de inventario](../../services/api/routes/inventory.py), [los schemas](../../services/api/schemas.py) y [las vistas del backoffice](../../uis/backoffice/src/app/inventory/InventoryPages.tsx). [event-schemas.json](event-schemas.json) es un JSON Schema Draft-07 para los 14 eventos diseñados en fase 2; las demás oportunidades siguen siendo catálogo sin esquema aprobado.

## Entidades, dimensiones y restricciones

- `Product`: SKU de un único `client`, con categoría moda, electrónica o cosmética. En el código actual es `SKU`: `sku` es el código de producto y `id` la PK numérica.
- `InboundOrder`: recepción de mercancía de un cliente, representada hoy por `StockEntry`. `OutboundOrder`: picking y despacho, representada parcialmente por `StockExit` de tipo `dispatch`; el mismo modelo incluye `loss`, que no es un despacho.
- `warehouse`: `los_angeles` o `zaragoza` en eventos. La API usa `LA` y `ZGZ`; el productor deberá mapearlos explícitamente. Para los futuros informes por país: `los_angeles` corresponde a Estados Unidos y `zaragoza` a España; no inferir país de la IP.
- `client`: marca dueña del SKU. El código guarda `client_name`, pero no dispone de `client_id` estable. Antes de emitir eventos obligatorios debe existir una identidad de cliente fiable y un mapeo inequívoco; no usar nombres como si fueran identificadores ni mezclar clientes.
- El contexto exige en `properties` de los eventos de inventario `warehouse`, `client_id`, `product_id` (SKU), `product_category` y `quantity`, además del envelope estándar. `product_id` debe identificar el SKU: el código actual ofrece el código `sku` y la FK numérica `sku_id`; acordar el contrato y su mapeo sin sustituir uno silenciosamente por el otro. Las categorías actuales son `fashion`, `electronics` y `cosmetics`; acordar su representación sin inventar valores del briefing.
- Toda modificación de stock pasa por una orden trazable a usuario; el stock se calcula por SKU y almacén. En telemetría no se guardan datos de transportista, destinatario final, `tracking_number` ni referencias libres de pedido. Tampoco contraseñas, tokens, emails, cuerpos, URLs con parámetros ni mensajes o trazas sin depurar. Usar identificadores de operario seudonimizados y acceso restringido, sujetos a política de retención por definir.
- En errores de validación puede no existir producto, cliente, almacén o cantidad válidos. No inventarlos ni registrar el payload: distinguir el evento operativo de validación con datos disponibles de los eventos obligatorios de inventario, que requieren las propiedades mínimas verificadas. Transporte y retención quedan para fases posteriores.

## Flujo y puntos de instrumentación

1. El usuario inicia sesión y accede al backoffice; `App` verifica la sesión y `get_current_user` protege las rutas de inventario. Registrar acceso, resultado de autenticación o rechazo; no afirmar que todo 401 significa expiración de token.
2. En productos o en formularios de entrada/salida se carga el catálogo; registrar vista, fallos de carga y selección de SKU. La UI muestra una señal fija de stock bajo con `current_stock <= 5`, que **no** es un mínimo configurado por cliente ni prueba una activación de umbral del briefing.
3. Al abrir el formulario se inicia el flujo; al enviar, el navegador valida campos y la API valida SKU, cantidad, almacén, referencia y reglas de salida. Contar intentos, validaciones fallidas y abandono sin almacenar valores libres. Una advertencia visual de stock insuficiente no equivale por sí sola a un rechazo del servidor.
4. `POST /inventory/orders/inbound` confirma una `StockEntry`; `POST /inventory/orders/outbound` rechaza cantidades superiores al stock o confirma una `StockExit`. Emitir eventos de éxito solo después del commit; separar `dispatch` de `loss` y correlacionar reintentos sin contar dos veces. El modelo actual no acredita por separado que el picking haya terminado.
5. Tras una salida confirmada, evaluar el cruce al mínimo configurado por cliente y SKU, no cada visualización. Hoy no existe esa configuración ni un emisor: requiere incorporarlos antes de capturar `stock_threshold_triggered`.
6. Un intento explícito de alterar `current_stock` fuera de una orden debe rechazarse y registrarse; el modelo actual calcula el stock y prohíbe campos extra, pero no existe una ruta dedicada de edición directa. Una respuesta 404/405 por sí sola no demuestra intención de modificar stock. La detección requiere un punto de captura fiable sin almacenar cuerpos.
7. El historial permite revisar movimientos, pero no hay flujo de conteo físico/auditoría ni comparación con stock real. `inventory_discrepancy_detected` requiere ese dato de origen; una salida `loss` no demuestra por sí sola una discrepancia detectada.

## Catálogo de eventos

Cada fila completa la frase: «Capturamos [evento] porque necesitamos saber [hipótesis], lo que nos permite tomar la decisión [decisión]». La columna de punto indica una ubicación propuesta, no un evento ya emitido. **Obligatorio** procede literalmente del contexto; **oportunidad** es una propuesta de este análisis.

### Obligatorios del contexto

| Evento | Clasificación | Punto / condición | Necesitamos saber | Decisión |
|---|---|---|---|---|
| `inbound_order_created` | Obligatorio | Backend, recepción confirmada tras commit. | Cuánto volumen entra por cliente y almacén. | Ana planifica capacidad y personal según las recepciones. |
| `outbound_order_created` | Obligatorio | Backend, picking y despacho completados; `dispatch` confirmado es una aproximación pendiente de verificar, no incluye `loss`. | Cuántos pedidos se procesan por cliente y almacén y a qué ritmo. | Ana identifica cuellos de botella antes de afectar al SLA. |
| `stock_threshold_triggered` | Obligatorio | Backend, cruce de stock desde al menos el mínimo hasta por debajo del mínimo configurado. No existe configuración todavía. | Con qué frecuencia un cliente queda sin stock disponible de un SKU. | Miguel alerta al cliente y al equipo comercial antes de un quiebre. |
| `direct_stock_edit_rejected` | Obligatorio | Backend, rechazo verificable de intento explícito de edición directa; punto aún por definir. | Si se intenta saltar la trazabilidad por almacén. | Reforzar capacitación o permisos donde corresponda. |
| `inventory_discrepancy_detected` | Obligatorio | Resultado de conteo físico o auditoría comparado con stock del sistema; flujo aún no implementado. | Qué SKU y almacenes presentan más diferencias. | Ana prioriza auditorías según la tasa de discrepancias. |

Un evento de cruce de umbral cuenta alertas, no demuestra por sí solo stock cero; para la hipótesis del contexto debe complementarse con el valor de stock verificado y, en fases posteriores, una definición explícita de falta de disponibilidad. Del mismo modo, una tasa de discrepancias necesita el denominador de conteos/auditorías, aún inexistente.

### Inventario y operaciones

| Evento | Clasificación | Punto / condición | Necesitamos saber | Decisión |
|---|---|---|---|---|
| `inventory_accessed` | Oportunidad | Acceso autenticado a una vista de inventario. | Qué almacenes y perfiles utilizan el módulo. | Dimensionar soporte y priorizar formación. |
| `inventory_catalog_viewed` | Oportunidad | Carga correcta del catálogo. | Con qué frecuencia se consulta la disponibilidad. | Priorizar rendimiento y capacidad de lectura. |
| `inventory_product_selected` | Oportunidad | Selección de SKU en un formulario. | Qué productos generan más consultas previas a movimientos. | Optimizar búsqueda y selección de SKU. |
| `inventory_order_form_started` | Oportunidad | Primera interacción con formulario de entrada o salida. | Cuántos flujos de cada tipo se inician. | Comparar inicios con finalizaciones y priorizar fricción. |
| `inventory_order_submitted` | Oportunidad | Intento de envío de formulario; distinguir dirección. | Cuántos intentos llegan a envío, sin contarlos como órdenes creadas. | Localizar el tramo entre inicio y resultado. |
| `inventory_order_validation_failed` | Oportunidad | Validación nativa o respuesta 422, por campo y código sanitizados. | Qué reglas impiden completar órdenes. | Mejorar instrucciones o validaciones del formulario. |
| `outbound_stock_insufficient` | Oportunidad | API rechaza salida por cantidad superior a stock disponible. | Cuántas salidas fallan por falta de stock y dónde. | Revisar reposición o desajustes de planificación. |
| `inventory_order_write_failed` | Oportunidad | Fallo técnico al confirmar entrada o salida, sin registrar excepciones crudas. | En qué parte se interrumpen los movimientos por fallos del servicio. | Priorizar reparación y recuperación operativa. |
| `inventory_order_abandoned` | Oportunidad | Formulario iniciado y salida sin éxito, con señal de navegación o timeout definida posteriormente. | En qué dirección se dejan flujos sin completar. | Simplificar pasos con mayor abandono; tratar cierres inciertos como estimación. |
| `inventory_stock_loss_recorded` | Oportunidad | `StockExit` de tipo `loss` confirmado. | Cuántas unidades se dan de baja por pérdida por SKU y almacén. | Investigar patrones de pérdida sin confundirlos con despachos. |
| `inventory_product_created` | Oportunidad | Alta de SKU confirmada. | Cómo crece el catálogo por cliente y categoría. | Planificar capacidad y mantenimiento del catálogo. |
| `inventory_product_duplicate_rejected` | Oportunidad | Alta de SKU rechazada por conflicto 409. | Con qué frecuencia se intenta dar de alta un SKU duplicado. | Mejorar búsqueda previa y guías de alta. |
| `inventory_history_viewed` | Oportunidad | Carga correcta del historial. | Con qué frecuencia se consultan movimientos anteriores. | Priorizar rendimiento y capacidad de auditoría. |

### Autenticación, navegación y fiabilidad

| Evento | Clasificación | Punto / condición | Necesitamos saber | Decisión |
|---|---|---|---|---|
| `login_attempted` | Oportunidad | Inicio de solicitud de login. | Cuántos intentos de acceso se producen. | Establecer denominador de fallos de acceso. |
| `login_succeeded` | Oportunidad | API emite sesión tras autenticación correcta. | Cuántos accesos legítimos se completan. | Evaluar disponibilidad del flujo de acceso. |
| `login_failed` | Oportunidad | API rechaza credenciales, con categoría genérica. | Cuántos intentos fallan sin identificar cuentas. | Ajustar soporte o medidas contra abuso sin facilitar enumeración. |
| `session_expired` | Oportunidad | Solo cuando se confirme vencimiento del token; 401 genérico se clasifica por separado. | Con qué frecuencia caducan sesiones durante el trabajo. | Ajustar duración de sesión y recuperación del flujo. |
| `auth_access_denied` | Oportunidad | Respuesta de acceso no autenticado o no autorizado, por código agregado. | Qué recursos encuentran barreras de acceso. | Revisar permisos o errores de integración sin exponer identidades. |
| `password_recovery_requested` | Oportunidad | Solicitud de recuperación recibida, sin email ni resultado de existencia de cuenta. | Cuánto se utiliza la recuperación. | Dimensionar soporte y mejorar acceso. |
| `backoffice_section_viewed` | Oportunidad | Cambio de sección o ruta normalizada, incluidas pestañas sin cambio de URL. | Qué secciones visitan más los operadores. | Priorizar mejoras de las áreas más utilizadas. |
| `api_request_completed` | Oportunidad | Middleware o cliente, duración, método, ruta parametrizada y estado. | Qué operaciones concentran latencia y errores. | Priorizar optimización y capacidad sin capturar parámetros. |
| `backoffice_view_loaded` | Oportunidad | Vista lista tras cargar datos; distinguir carga inicial y navegación. | Qué vistas tardan más en estar utilizables. | Optimizar módulos o consultas lentas. |
| `frontend_unhandled_error` | Oportunidad | Error no capturado o promesa rechazada, clasificados y saneados. | Qué fallos inesperados interrumpen el trabajo. | Priorizar correcciones por frecuencia y sección. |
| `frontend_data_load_failed` | Oportunidad | Fallo controlado al cargar datos de una vista. | Qué consultas impiden mostrar contenido. | Reparar servicios o mejorar estados de recuperación. |
| `frontend_retry_requested` | Oportunidad | Operador pulsa Reintentar tras un fallo. | Con qué frecuencia se intenta recuperar una carga fallida. | Mejorar recuperación y detectar fallos persistentes. |
| `frontend_chunk_load_failed` | Oportunidad | Fallo de importación diferida, sin URL ni excepción cruda. | Si las vistas diferidas fallan al descargarse. | Ajustar despliegue, caché de assets o recuperación. |

### Otras secciones existentes

| Evento | Clasificación | Punto / condición | Necesitamos saber | Decisión |
|---|---|---|---|---|
| `incident_created` | Oportunidad | API confirma creación de incidencia. | Cuántas incidencias se registran por categoría y sede. | Distribuir capacidad de atención. |
| `incident_status_changed` | Oportunidad | API confirma transición de estado. | Cómo avanzan las incidencias entre estados. | Identificar acumulación y priorizar seguimiento. |
| `incident_analysis_completed` | Oportunidad | Análisis CSV finalizado, solo conteos agregados. | Qué proporción de registros analizados es inválida. | Priorizar corrección de calidad de datos. |
| `supplier_created` | Oportunidad | API confirma alta de proveedor. | Cómo crece el directorio por país y categoría. | Identificar necesidades de cobertura de proveedores. |
| `supplier_status_changed` | Oportunidad | API confirma cambio de estado de proveedor. | Con qué frecuencia se suspenden o reactivan proveedores. | Revisar continuidad de cobertura operativa. |
| `supplier_rate_changed` | Oportunidad | API confirma actualización de tarifa, sin registrar contacto ni notas. | Con qué frecuencia se revisan las tarifas. | Priorizar revisión comercial de tarifas desactualizadas. |

## Envelope y contrato de fase 2

Todo evento contiene las ocho claves siguientes, incluso cuando una de las tres admite `null`. El esquema valida con Draft-07 y comprobación activa de `format`; `additionalProperties: false` cierra tanto el envelope como el payload elegido por `event_type`.

| Campo | Tipo | Obligatorio | Significado |
|---|---|---|---|
| `eventId` | UUID | Sí | Identificador único de la ocurrencia; conservarlo al reintentar para deduplicar. |
| `timestamp` | ISO 8601 UTC | Sí | Momento de ocurrencia, con sufijo `Z`. |
| `sessionId` | UUID o `null` | Sí | Identificador opaco de sesión; `null` si no existe o no se conoce. Nunca un JWT. |
| `userId` | String o `null` | Sí | Seudónimo del usuario autenticado, `u_` más 64 hexadecimales; `null` si no se ha identificado de forma fiable. |
| `event_type` | String | Sí | Nombre entidad_accion; selecciona un único esquema de payload. |
| `schemaVersion` | String | Sí | Versión del contrato: `1.0.0`. |
| `requestId` | UUID o `null` | Sí | Correlación de una solicitud; `null` si el evento no está asociado a una solicitud. |
| `properties` | Objeto | Sí | Datos específicos según el allowlist del evento, incluso si está vacío. |

Para `login_attempted` y `login_failed`, `userId` debe ser `null`; `login_succeeded` requiere un seudónimo y un `sessionId` opaco. La aplicación actual no genera ese identificador de sesión: incorporarlo es una dependencia futura, no se debe derivar del JWT ni fingir que ya existe. Almacenar el `user_uuid` solo en el sistema de identidad; generar el seudónimo en servidor con HMAC-SHA-256 y una clave secreta gestionada fuera de eventos y repositorio. La seudonimización no elimina la consideración de dato personal: limitar acceso, gestionar rotación de clave y definir retención antes del despliegue. Los eventos anónimos nunca deben incluir emails, IP ni identidad proporcionada en el login.

El origen confiable debe resolver `client_id` y el código SKU real, comprobar el propietario único y mapear `LA`/`ZGZ` a `los_angeles`/`zaragoza`. El contrato restringe `client_id` a identificadores opacos alfanuméricos, `_` o `-`, y `product_id` a códigos alfanuméricos, `.`, `_` o `-`. El modelo actual admite otros valores: se necesita validación/migración o decisión de compatibilidad; no transformar cadenas arbitrariamente ni inventar IDs. No se usa el nombre de cliente como ID. SKU y cliente siguen siendo datos comerciales sensibles; restringir acceso.

### Allowlists y sensibilidad

Todas las claves enumeradas son obligatorias salvo que se marque «opcional». Las descripciones, tipos y restricciones completas de cada propiedad están en el JSON Schema, incluidos los `$ref` a `definitions`; `x-classification`, `x-category` y `x-sensitive-data` son anotaciones documentales, no reglas de validación.

| Evento | Allowlist de `properties` | Datos sensibles y tratamiento |
|---|---|---|
| `inbound_order_created` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`, `order_id` | Cliente/SKU comerciales; acceso restringido. No incluir referencia libre. |
| `outbound_order_created` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`, `order_id` | Cliente/SKU comerciales; sin transportista, destinatario ni tracking. |
| `stock_threshold_triggered` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`, `threshold` | Cliente/SKU y stock comerciales; acceso restringido. |
| `direct_stock_edit_rejected` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity` | Cliente/SKU comerciales; no conservar cuerpo ni valores no verificados. |
| `inventory_discrepancy_detected` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`; `direction` opcional | Cliente/SKU y discrepancia comerciales; acceso restringido. |
| `inventory_stock_loss_recorded` | `warehouse`, `client_id`, `product_id`, `product_category`, `quantity`, `order_id` | Cliente/SKU comerciales; sin datos de destino ni tracking. |
| `login_attempted` | Sin claves: `{}` | Sin email, contraseña, token, IP ni identidad propuesta. |
| `login_succeeded` | Sin claves: `{}` | Solo seudónimo HMAC y UUID opaco en el envelope; sin credenciales. |
| `login_failed` | `reason` | Motivo agregado de lista cerrada; no revelar cuenta o credenciales. |
| `session_expired` | Sin claves: `{}` | Seudónimo opcional; sin JWT ni detalle de credenciales. |
| `backoffice_section_viewed` | `section` | Sección de lista cerrada; sin URL ni parámetros. |
| `api_request_completed` | `route_group`, `method`, `status_code`, `duration_ms` | Ruta agrupada; sin IDs, query, cuerpos ni cabeceras. |
| `frontend_unhandled_error` | `section`, `error_kind` | Categorías cerradas; sin mensaje, stack, URL ni payload. |
| `frontend_retry_requested` | `section` | Sección normalizada; sin contenido ni error crudo. |

`quantity` significa unidades recibidas/despachadas en órdenes, stock tras el cruce en umbral, cantidad pertinente verificada en edición rechazada, diferencia absoluta en auditoría y unidades de pérdida en la baja. El contrato de umbral exige `threshold`, pero JSON Schema no compara ambos campos: el productor debe confirmar que `quantity < threshold` y que hubo cruce real. `direction`, cuando se conoce en una discrepancia, indica si el conteo físico es menor o mayor que el del sistema.

Si una edición directa rechazada no proporciona los cinco valores mínimos verificables, **no se puede emitir un evento obligatorio válido**: no rellenar `quantity`, cliente o SKU con valores ficticios. Falta definir una señal de seguridad separada para estos casos y resolver el requisito del briefing antes de la implementación. La validación no garantiza la procedencia de identificadores ni elimina secretos de valores permitidos: el productor debe construir el objeto a partir de datos confiables y de listas cerradas, no copiar el payload de entrada. Las comprobaciones de formato requieren un validador con `FormatChecker` o equivalente.

## Estrategia de entrega de fase 3

La tabla cubre los 14 eventos que tienen esquema en fase 2; las demás oportunidades del catálogo aún no tienen contrato ni modo de entrega aprobado. **Stream** propone procesar continuamente al recibir el evento; **batch** propone enviar y procesar grupos periódicos. Los intervalos siguientes son objetivos iniciales de diseño, no SLA acordados ni capacidades implementadas.

| Evento | Modo | Justificación por decisión y urgencia |
|---|---|---|
| `inbound_order_created` | Stream | Ana necesita ver pronto el volumen recibido y ajustar capacidad y personal durante la jornada; se propone disponibilidad en unos 30 segundos. |
| `outbound_order_created` | Stream | El ritmo de despachos permite detectar un atasco antes de afectar al SLA, no solo al cierre del día; objetivo inicial de 30 segundos. |
| `stock_threshold_triggered` | Stream | La alerta al cliente y al equipo comercial debe llegar antes del quiebre; objetivo inicial de 30 segundos tras el cruce confirmado. |
| `direct_stock_edit_rejected` | Stream | Un patrón de intentos de eludir trazabilidad puede requerir revisión rápida de permisos o capacitación; objetivo inicial de un minuto. |
| `inventory_discrepancy_detected` | Stream | Una diferencia confirmada puede afectar la exactitud del inventario y requiere atención operativa durante la jornada; objetivo inicial de un minuto. |
| `inventory_stock_loss_recorded` | Stream | Una pérdida confirmada cambia la disponibilidad y puede requerir revisión operativa pronta; objetivo inicial de un minuto. |
| `login_attempted` | Batch, cada 5 minutos | El denominador de accesos sirve para análisis de tendencias; la detección rápida de fallos usa el evento separado `login_failed`. |
| `login_succeeded` | Batch, cada 5 minutos | La tasa de acceso completado se usa para evaluar el flujo, sin necesidad de una decisión individual inmediata. |
| `login_failed` | Stream | Un aumento de rechazos puede indicar un problema de acceso o abuso y requiere revisión rápida; objetivo inicial de un minuto. |
| `session_expired` | Batch, cada 15 minutos | Ajustar duración de sesiones o el flujo de recuperación es una decisión de tendencia, no una intervención por cada sesión. |
| `backoffice_section_viewed` | Batch, cada 15 minutos | La priorización de mejoras por uso de secciones no necesita datos segundo a segundo. |
| `api_request_completed` | Stream | Los errores y aumentos de latencia pueden impedir la operación actual; objetivo inicial de un minuto para indicadores y alertas. El muestreo de solicitudes exitosas se detalla abajo. |
| `frontend_unhandled_error` | Stream | Una ráfaga de errores inesperados puede bloquear operadores y debe detectarse durante el incidente; objetivo inicial de un minuto. |
| `frontend_retry_requested` | Batch, cada 5 minutos | La frecuencia de reintentos ayuda a priorizar mejoras de recuperación; el fallo inesperado urgente se observa por separado. |

En stream, la captura y los reintentos deben ser asíncronos y no bloquear la respuesta de negocio. Para movimientos confirmados se propone un outbox persistente en la misma transacción que el cambio y emisión posterior; para rechazos o errores sin transacción de inventario se necesita un registro seguro independiente. No se ha implementado ninguno. Conservar el mismo `eventId` al reintentar y deduplicar en el consumidor; no prometer entrega exactamente una vez ni confundir una entrega duplicada con una nueva operación. Para batch, mantener el timestamp original, agrupar por intervalo y contemplar retrasos; los eventos de navegador al cerrar una pestaña son de mejor esfuerzo. El almacenamiento temporal, cifrado, límites de cola, recuperación y alertas por pérdida deben diseñarse antes de producción.

Como `login_failed` llega antes que `login_attempted` y `login_succeeded`, calcular tasas solo sobre ventanas cerradas cuando hayan llegado ambos flujos, incorporando eventos tardíos y documentando los faltantes. Los conteos de errores no identifican cuentas ni IP: permiten detectar tendencias agregadas, pero no atribuir un ataque a una persona ni sustituir controles de seguridad.

### Throttle, debounce y volumen

- **Eventos de negocio y seguridad:** no aplicar muestreo, throttle ni debounce a las cinco métricas obligatorias, a `inventory_stock_loss_recorded` ni a intentos/resultados de login. Cada operación o intento real cuenta; deduplicar únicamente reentregas con el mismo `eventId`. Un cruce de umbral se emite una vez por transición verificada, no por cada consulta mientras el stock sigue bajo.
- **API de alta frecuencia:** propuesta inicial: conservar el 100 % de respuestas con estado 400–599 y el 100 % de solicitudes de al menos 1000 ms; de las demás respuestas, muestrear uniformemente el 10 %. La selección determinista usa un hash de `requestId`, o de `eventId` si no existe `requestId`, nunca de `userId`. Estos parámetros son provisionales y deben ajustarse con tráfico real. Conservar la política y versión de muestreo fuera del envelope para cada ventana: estimar totales y percentiles con pesos inversos según el estrato, y no presentar como conteos reales los eventos retenidos. Si faltan esas probabilidades, no calcular métricas poblacionales. Alertas de 4xx/5xx y solicitudes lentas conservan todas las observaciones, sujetas a disponibilidad de entrega.
- **Errores de frontend:** no descartar ocurrencias para contabilizar impacto. Limitar solo las notificaciones derivadas: como máximo una alerta por minuto y combinación de `section` y `error_kind`; mantener el conteo real de eventos en la ventana. Este throttle no modifica el esquema ni sustituye la cola de eventos.
- **Navegación:** encolar un evento por transición lógica confirmada; deduplicar llamadas repetidas de la misma transición antes de añadirla al lote, no visitas distintas aunque ocurran seguidas. No capturar renders, movimientos de ratón ni pulsaciones. Envío batch cada 15 minutos y al salir de la vista cuando sea posible; una pestaña cerrada sin entrega puede producir infracontaje.
- **Reintentos de UI:** registrar cada pulsación real y agruparla en batch; no aplicar debounce temporal que confunda pulsaciones distintas. La interfaz puede impedir dobles envíos de solicitud, pero una pulsación no demuestra que el reintento haya tenido éxito.

### Riesgos y exclusiones

- **Señales descartadas:** eventos por cada render del indicador de stock bajo, movimiento de ratón o tecla pulsada. Su volumen y duplicación no aportan una decisión proporcional al coste; el cruce de umbral y la navegación confirmada tienen significado operativo más claro.
- **Datos excluidos por privacidad:** credenciales, tokens, emails, IP, destinatarios, transportistas, números de seguimiento, referencias libres, cuerpos de solicitud, URLs con parámetros, mensajes y trazas de error sin sanear. No se capturan valores de formularios ni contenido de incidencias; la seudonimización del operario sigue siendo dato personal.
- **Datos excluidos por coste:** capturas de pantalla, grabaciones de sesión, payloads completos y trazas individuales de todos los renders. La observabilidad se limita a categorías cerradas, conteos y duraciones necesarias para las decisiones descritas.
- **Fidelidad de métricas:** el muestreo de API y la pérdida de lotes de navegador pueden sesgar tasas o percentiles; registrar cobertura, probabilidades, retrasos y pérdidas de entrega antes de interpretar indicadores. Las notificaciones limitadas no representan el número de errores.
- **Consistencia y captura:** stream no garantiza inmediatez ni entrega en orden. Caídas, reintentos, concurrencia o relojes desajustados pueden producir retrasos o duplicados; la estrategia de outbox, deduplicación, ventanas y reconciliación requiere implementación y pruebas. No emitir el evento obligatorio de edición directa sin sus dimensiones verificadas: actualmente esa cobertura queda bloqueada, no resuelta con el transporte.
- **Dependencias de negocio:** no hay `client_id` estable, mínimos configurables ni auditoría física; además falta evidencia del picking completado. Hasta resolverlos no puede afirmarse que se emitan correctamente todas las métricas obligatorias. La elección stream no crea esas capacidades.

## Dependencias y decisiones pendientes

## Dependencias y decisiones pendientes

1. Acordar identidad estable de cliente y SKU, procedencia de categorías y dimensiones fiables para solicitudes rechazadas. Las propiedades mínimas obligatorias no deben rellenarse con datos no verificados.
2. Diseñar y construir mínimos configurables por cliente, conteo físico/auditoría y detección fiable de edición directa; ninguno está implementado por esta fase. Definir asimismo la señal de picking completado para no confundir un registro de salida con el proceso completo.
3. Definir cuándo un flujo se considera abandonado, cómo distinguir sesión expirada de otros 401, y cómo evitar duplicados de eventos cliente/servidor y reintentos. El usuario autenticado se obtiene del servidor; no confiar en un identificador enviado por el navegador.
4. El seed del contexto futuro pide 8–10 SKU, al menos 2 clientes, las 3 categorías y ambos almacenes, 15–20 entradas, 15–20 salidas, 2 activaciones de umbral y 1 discrepancia. No se crean datos ni se afirman estos casos en la fase 1; los dos últimos requieren primero las capacidades indicadas.