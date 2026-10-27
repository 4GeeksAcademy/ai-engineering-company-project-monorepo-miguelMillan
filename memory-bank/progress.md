# Progress Log — TrackFlow

## HITO05 — inventario backoffice — 2026-10-27
- Añadida la capa de llamadas a `/inventory` con autenticación existente y vistas de productos, entradas, salidas e historial; protegidas por el guard global del backoffice.
- Conservado `memory-bank/history/HITO05-CONTEXT.md` sin sobrescribirlo. Archivada la versión vigente usada en `memory-bank/history/HITO05-INVENTORY-UI-CONTEXT-2026-10-27.md`; `cmp` confirma que coincide byte a byte con `CONTEXT.md`.
- Validaciones: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint`, correctos.
- Ajustado `inventoryRequest` para mostrar el cuerpo de error tanto en JSON como en texto plano; build y lint repetidos correctamente.
- Bloqueo: `services/api` no expone las rutas `/inventory`; la integración HTTP requiere un backend compatible disponible en la URL configurada. No se verificaron llamadas reales ni flujos en navegador.

## API-042 / FE-019 — cobertura backoffice y utilidades — 2026-10-27
- Actualizado `TESTING.md` con el plan, grupos `/suppliers` y `/api/incidents`, casos de éxito/límite/fallo y ejecución independiente de Jest. Añadidos `test_incidents.py` y fixture autenticado; la suite existente de proveedores se utiliza como segundo grupo.
- Documentado en `TESTING.md` un caso concreto identificado con apoyo de IA: rechazar tokens de recuperación como sesión y enlaces de reset reutilizados; ambos están cubiertos por pruebas existentes.
- Añadidos tests bajo `uis/backoffice/src/__tests__/` para sesión, `positiveRate`, `errorMessage` e `incidentError`. Extraídos los helpers puros a módulos pequeños y conservados sus exports desde las utilidades existentes. Excluidos los tests del tsconfig de producción y conservado su tsconfig Jest.
- Cobertura: `routes/suppliers.py` 100% (29 tests), `routes/incidents.py` 100% (4 tests), Jest 100% en `session.ts`, `supplierUtils.ts` e `incidentErrors.ts` (14 tests). Suite FastAPI completa: 66 tests correctos. Build y lint de backoffice correctos.
- Advertencia no bloqueante de deprecación Starlette/httpx. Archivos de cobertura excluidos e ignorados. Contexto archivado íntegro en `memory-bank/history/API-042-FE-019-CONTEXT.md`.

## AUTH-088 — pruebas de autenticación — 2026-10-27
- Añadido `TESTING.md` antes de escribir los tests con casos previstos de éxito, límites y fallos para registro, login, `/auth/me`, recuperación, reset, cambio y validación de tokens, además de comandos y justificación.
- Añadidos módulos pytest por endpoint bajo `services/api/tests/`, fixtures con TestClient y TinyDB temporal, y `pytest-cov` en el grupo dev del proyecto API. Eliminadas comprobaciones de OpenAPI/serialización del test de login anterior.
- Configurado Jest en `uis/backoffice`; aislados y probados los helpers de localStorage en `src/utils/session.ts`, manteniendo sus exports previos desde `auth.ts`.
- Validaciones: `uv run --directory services/api pytest --cov=routes.auth --cov-report=term-missing --cov-fail-under=70`: 62 correctas, 98,11% en `routes/auth.py`; `npm --prefix uis/backoffice test -- --coverage --runInBand`: 4 correctas, 100% en `session.ts`; build y lint de backoffice correctos. Una advertencia no bloqueante de deprecación Starlette/httpx.
- Contexto archivado íntegro en `memory-bank/history/AUTH-088-CONTEXT.md`. Sin cambios en lógica de autenticación de producción; el refactor separa únicamente helpers de sesión para probarlos con Jest.

## Skill — auditoría de gestión de errores — 2026-10-27
- Creada la skill invocable `.agents/skills/error-handling-audit/SKILL.md`, adaptada a la estructura actual del monorepo: TypeScript/React/Vite, FastAPI/Python, gateway Node.js y scripts Python. Define revisión de solo lectura, ocho categorías de hallazgo, criterios por tecnología, severidades y formato de informe; no ejecuta remediaciones.
- Validación: inspección del frontmatter `name`/`description`, ubicación y contenido de la skill. No se modificó código de aplicaciones ni se ejecutaron auditorías o pruebas.

## AUTH-02 — autenticación frontend — 2026-10-27
- Integrados en `uis/backoffice` el registro (`POST /users` con campos opcionales de perfil, luego login automático), persistencia JWT en `localStorage`, vista de perfil (`GET /auth/me`, `PUT /profiles/me`) y guard de sesión para todas las vistas internas. Login, registro, recuperación y restablecimiento siguen siendo públicos; el website no fue modificado.
- Centralizado el uso Bearer y el manejo de 401 en las llamadas de autenticación, incidencias, proveedores y análisis/exportación CSV. El cierre de sesión elimina el token y redirige a `/login`; Vite reenvía `/users` y `/profiles` a FastAPI.
- Validaciones: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint` correctos; diagnósticos del editor sin errores. No se ejecutó prueba de navegador.
- El contexto se archivó íntegro en `memory-bank/history/AUTH02-CONTEXT.md`. Este checkout tiene un backoffice React/Vite; no hay fuentes ni paquete Next.js en `uis/talent-pipeline-tracker`, por lo que la integración se hizo en el frontend interno existente, sin crear otra aplicación.

## Históricos — consolidación del contexto de incidencias — 2026-10-27
- Eliminados los históricos duplicados `INCIDENTS-BACKEND-CONTEXT.md` y `INCIDENTS-SEED-CONTEXT.md`; ambos eran idénticos byte a byte a `INCIDENTS-FRONTEND-CONTEXT.md`, que se conserva como copia única del briefing. `INCIDENTS-MANAGER-MODEL-CONTEXT.md` se conserva porque su contenido es distinto.
- Verificación previa: SHA-256 idéntico en los tres duplicados; estado Git previo mostraba `CONTEXT.md` modificado por el usuario. No se modificó ese archivo.

## Gestor de incidencias — carga real del historico — 2026-10-22
- Ante el panel con metricas en cero, confirmada la base activa de FastAPI mediante sus archivos abiertos: `services/api/data/suppliers.json`. La tabla incidents estaba vacia; las cargas anteriores solo se habian realizado en bases temporales.
- Detenido el supervisor Node y confirmada la salida de sus procesos antes de escribir en TinyDB. Ejecutado el seed existente sobre la base real con ruta --db explicita: 100 filas leidas, 95 incidencias insertadas, 0 duplicados y 5 filas invalidas descartadas (4, 26, 43, 69 y 98). No se mostraron datos personales.
- Comprobacion inmediata de la base: total 95; estados open=29, resolved=52, discarded=14; categorias lost_parcel=14, carrier_issue=45, delivery_failure=19, returns_issue=17; origen customer=95; sedes la_office=50 y zaragoza_office=45. Todos los registros previos de todas las tablas conservados sin modificaciones.
- Backend reiniciado con `npm --prefix services/api run dev`: Node 8080 y FastAPI 8081 con startup completado. Frontend no modificado; siguiente paso del usuario: abrir Incidencias y pulsar Actualizar, iniciando sesion de nuevo si procede. La prueba visual sigue pendiente por las dependencias de Chromium.
- Contexto de negocio sin cambios respecto al historico `memory-bank/history/INCIDENTS-FRONTEND-CONTEXT.md`; se conserva la copia existente sin sobrescribir historicos. No se tocaron configuraciones secretas ni se creo commit.

## Gestor de incidencias — frontend — 2026-10-22
- Integradas vistas Registrar incidencia e Incidencias en el menu existente de `uis/backoffice`, manteniendo analizador CSV, proveedores y autenticacion. Nuevos componentes PascalCase en `src/app/incidents`: IncidentRegistration, IncidentDirectory e IncidentSummaryPanel; utilidades/tipos/catalogos/peticiones en `src/utils/incidents.ts`.
- Formulario con seis campos editables del modelo y tres metadatos de solo lectura no enviados. Branch siempre visible y obligatorio, cinco sedes con etiquetas exactas y Central incluido; resaltado cuando origin=branch. Validacion local de campos requeridos/textos en blanco, errores por campo, bloqueo sincrono de duplicados, carga y controles deshabilitados, reset y confirmacion de exito.
- Listado con filtros status/origin/branch, carga, error con reintento y mensajes de vacio o sin coincidencias. Detalle desplegable con descripcion/UUID/fechas UTC; selector con transiciones del contexto y estados finales bloqueados. Cambio optimista con rollback a la fila anterior y error local si falla; al completar se actualiza resumen y se elimina la fila si deja de cumplir el filtro de estado. Filtros y refresco bloqueados durante actualizaciones pendientes.
- Resumen por estado/categoria/origen/sede y total, con peticion, carga, error y reintento independientes del listado. Catalogos exactos del contexto, peticiones Bearer, sesion caducada limpiada y mensajes locales seguros en lugar de texto tecnico del backend. Etiquetas/fieldset/legend, aria-invalid/describedby, alert/status, foco visible, controles tactiles y utilidades Tailwind; tabla con scroll accesible horizontal.
- Verificaciones ejecutadas: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint` correctos. Corregida una advertencia set-state-in-effect del resumen derivando la carga por version de peticion; repetidos build/lint sin advertencias. Diagnosticos del editor sin errores en las cinco rutas tocadas.
- Comprobacion compilada en memoria con Vite y renderToStaticMarkup correcta: estados/origenes/sedes, nueve categorias, cuatro transiciones, seis campos y tres metadatos readonly, cinco opciones de sede/branch obligatorio, cargas iniciales independientes, Bearer, traduccion segura de errores 400 por campo, 500, red y 401 con limpieza de sesion. Primera configuracion virtual de la comprobacion no resolvio la entrada; corregida para usar archivo real, sin modificar fuentes ni crear tests.
- Bloqueo: intento de lanzar Chromium fallo por libatk-1.0.so.0 ausente. No se verificaron interacciones de navegador, envio/reset, errores por campo interactivos, rollback, filtros ni responsive visual/capturas. Instalar dependencias Linux de Chromium con permisos adecuados y validar estos flujos antes de aceptar visualmente. PageSpeed no medido sin URL publica. No se crearon archivos de pruebas ni se ejecuto la suite completa.
- README del backoffice actualizado. Contexto archivado integro en `memory-bank/history/INCIDENTS-FRONTEND-CONTEXT.md`; cmp confirma identidad byte a byte. Frontend existente disponible en `http://localhost:5174` (HTTP 200); requiere backend/gateway activo y sesion para usar los datos.
- Estado: implementacion del bloque frontend completada, comprobaciones de navegador pendientes por entorno. Sin cambios backend, base real ni secretos; sin commit. Siguiente paso: resolver el bloqueo visual o recibir el siguiente bloque del usuario.

## Gestor de incidencias — backend — 2026-10-22
- Implementados en `services/api/routes/incidents.py` los cinco endpoints autenticados: POST/GET `/api/incidents`, GET `/api/incidents/{id}`, PATCH `/api/incidents/{id}/status` y GET `/api/incidents/summary`. Registrados en FastAPI y conectados al gateway Node existente; analizador y exportacion conservan sus rutas.
- Reutiliza el modelo Incident y la tabla `incidents` del seed, con bloqueo compartido. Creacion con metadatos automaticos, filtros opcionales combinables status/origin/branch/category, detalle 404 y resumen completo con total y conteos por estado/categoria/origen/sede, incluidos ceros en base vacia. `/summary` se registra antes de `/{id}`.
- PATCH solo admite status; valida las cuatro transiciones del contexto, rechaza permanencias y cambios desde estados finales, y actualiza exclusivamente status/updated_at. Modelo de entrada `IncidentStatusUpdate` con campos extra prohibidos.
- Errores de validacion del gestor devuelven 400 con campo/mensaje claro y sin valores de entrada; transiciones invalidas 400 con campo status. Contrato 422 de otras rutas conservado. Manejador global de excepciones FastAPI devuelve 500 generico sin stack trace ni detalles internos. Gateway permite Authorization en CORS, devuelve 503 generico si falla el upstream y oculta mensajes internos en errores inesperados del analizador.
- Verificacion HTTP aislada con TestClient y base temporal correcta: 401 en cinco rutas sin sesion, listado/resumen vacios, campos obligatorios y valores invalidos 400, creacion 201, detalle 200/404, filtros validos combinados/invalidos, 16 combinaciones de transicion, inmutabilidad de otros campos, actualizacion de fecha, UUID/JSON invalidos 400, PATCH con campos extra 400, excepcion simulada 500 sin detalle privado y 422 anterior conservado. Primer intento detenido por email sintetico con dominio .test reservado; corregido solo el dato de validacion a example.com y repetido correctamente.
- Integracion ejecutada con Node real -> Uvicorn en puertos temporales y base sembrada: Bearer, listado/filtros, creacion/detalle/PATCH, 400, summary exacto de 95 registros (estados 29/0/52/14, carrier_issue 45, customer 95, sedes 50/45), analizador vacio 422, exportacion sin datos 404 y health 200. Procesos temporales cerrados al terminar.
- `PYTHONDONTWRITEBYTECODE=1 uv run --directory services/api pytest -q tests/test_auth.py`: 6 correctas, advertencia Starlette/httpx preexistente. `node --check services/api/src/server.js`: correcto. Diagnosticos del editor sin errores en main/modelos/router/gateway. No se crearon archivos nuevos de pruebas ni se ejecuto la suite completa.
- Documentados endpoints, errores, resumen y proxy en README de API. Contexto archivado integro en `memory-bank/history/INCIDENTS-BACKEND-CONTEXT.md`; `cmp` confirma identidad byte a byte. No se alteraron los historicos anteriores ni el contexto raiz del usuario.
- Estado: bloque backend completado. Sin cambios frontend, base real o secretos; sin commit. Siguiente paso: recibir el siguiente bloque del usuario.

## Gestor de incidencias — seed historico — 2026-10-22
- Creado `scripts/seed_incidents.py`, ejecutable con `uv run --project services/api python scripts/seed_incidents.py [csv_path] [--db ruta]`. CSV por defecto: `scripts/incidents-trackflow.csv`; misma base que la API mediante `database.open_database`, tabla separada `incidents`. La API debe estar detenida durante el seed.
- Extraidas las reglas originales, catalogos y lectura/validacion CSV a `packages/shared/incident_validation.py`; el analizador anterior las reutiliza conservando sus importaciones y resultados. Solo el seed permite la ausencia de `incident_id` para aplicar el fallback del briefing.
- Mapeos completos previos al insert: estados OPEN/CLOSED/DISCARDED, cinco categorias CSV a cuatro categorias del modelo, US/ES a la_office/zaragoza_office, descripcion literal, titulo de primeros 120 caracteres recortados, fecha a medianoche UTC y ambas fechas iguales. Origen fijo `customer`; validacion final mediante `Incident`.
- Idempotencia mediante UUID5 estable derivado de `incident_id`; no se almacena ese campo CSV en el modelo ni se necesita una tabla auxiliar. Si falta, se usa titulo + fecha de creacion. Los registros existentes no se sobrescriben. Las filas invalidas o no mapeables se descartan y se resumen al final solo con fila/motivos, sin correos ni contenido de filas.
- Validaciones ejecutadas con Python en memoria y bases temporales: analizador conserva 100/95/5 y CSAT 3.06; seed real dos veces inserta 95 y luego 0 (95 omitidas), descartando filas 4, 26, 43, 69 y 98. Estados 29/52/14, categorias 14/45/19/17, sedes 50/45, origen, fechas, campos del modelo y tablas ajenas conservadas.
- Verificada CLI desde otro directorio con CSV por defecto y --db temporal, sin emails en stdout/stderr; fallback sin ID, duplicados internos, BOM, descripcion literal, titulo 120, rechazo de titulo vacio tras transformacion, ID estable aunque cambie descripcion y error de encabezados con codigo 2 antes de abrir la base. El analizador sigue rechazando CSV sin cabecera incident_id.
- Documentados comandos, mapeos, idempotencia, descartes y limitacion de concurrencia en ambos README de scripts. No se ejecuto el seed sobre la base real ni se crearon archivos nuevos de pruebas; no se ejecuto la suite completa.
- Archivado el contexto vigente en `memory-bank/history/INCIDENTS-SEED-CONTEXT.md`; `cmp` confirma copia exacta byte a byte. No se modificaron el contexto raiz del usuario ni el historico anterior del bloque modelo.
- Estado: bloque seed completado; endpoints y frontend quedan fuera de este bloque. Siguiente paso: recibir el siguiente bloque del usuario. Sin cambios en secretos ni otras rutas protegidas; sin commit.

## Gestor de incidencias — bloque modelo — 2026-10-22
- Implementado exclusivamente el bloque de modelo de datos solicitado en `services/api/models.py`, reutilizando Pydantic y sin modificar el analizador de CSV anterior ni la autenticacion.
- `IncidentCreate` requiere titulo, descripcion, categoria, origen y sede. Rechaza campos extra, textos vacios o compuestos solo por espacios, y valores ajenos a los catalogos del contexto. Conserva literalmente la descripcion para el futuro seed.
- Catalogos: nueve categorias exactas del briefing, estados `open`/`in_progress`/`resolved`/`discarded`, origenes `customer`/`branch`/`internal` y cinco sedes exactas. `branch` es obligatorio para todos los origenes; se debe indicar `central` cuando no corresponda una sede especifica. Estado inicial por defecto `open`.
- `Incident` genera UUID4 y fechas UTC; `updated_at` comienza igual a `created_at` y se actualiza automaticamente en asignaciones validadas de campos de negocio. ID y fecha de creacion no se pueden reasignar. Las fechas requieren zona horaria. El esquema de entrada no acepta ID ni fechas del cliente.
- Validacion ejecutada con `uv run --directory services/api python -` y datos sinteticos en memoria: UUID distintos, fechas UTC, campos obligatorios, valores invalidos, 135 combinaciones categoria/origen/sede, actualizacion automatica, rechazo de asignacion de estado invalido sin mutacion, metadatos protegidos, fechas sin zona rechazadas y round-trip JSON correctos. No se crearon archivos de pruebas ni se ejecuto la suite completa.
- Contexto de este bloque archivado integro en `memory-bank/history/INCIDENTS-MANAGER-MODEL-CONTEXT.md`, sin cambiar el `CONTEXT.md` actualizado por el usuario.
- Estado: bloque de modelo completado; sin endpoints, persistencia, seed, transiciones de ciclo de vida ni cambios frontend. Siguiente paso: recibir el siguiente bloque del usuario. Sin cambios en secretos ni otras rutas protegidas; sin commit.

## AUTH-03 — 2026-10-22
- Implementados `POST /auth/forgot-password`, `/auth/reset-password` y `/auth/change-password` sobre FastAPI/TinyDB, conforme al `CONTEXT.md` actualizado (Resend).
- Recuperacion con respuesta 200 uniforme para emails validos existentes o desconocidos, envio en segundo plano por API HTTPS de Resend, plantilla HTML movil y texto plano. Los errores del proveedor no revelan la existencia de cuentas.
- JWT de recuperacion con proposito separado y expiracion de 30 minutos; solo el SHA-256 pendiente se persiste en el usuario. Escritura de contrasena bcrypt y consumo del token bajo bloqueo, en una unica actualizacion. Emitir otro token o cambiar la contrasena invalida el anterior; no se revocan los JWT de sesion previos.
- Frontend React/Vite: login minimo (no existia), enlace de recuperacion, `/forgot-password`, `/reset-password` y `/account/change-password`. Incluye confirmacion, bloqueo de duplicados, errores accesibles, verificacion de sesion y redireccion con exito. Bearer en proveedores, sesion por pestana y cierre de sesion.
- Documentadas `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `PASSWORD_RESET_URL`, restricciones del proveedor, proxy `/auth` y comandos Codespaces. `.gitignore` ahora excluye `.env` y `.env.*` en cualquier carpeta; no se leyo ni modifico el `.env` del usuario.
- Validaciones ejecutadas: `uv run --directory services/api pytest -q tests/test_auth.py`: 6 correctas (advertencia Starlette/httpx preexistente). Comprobacion HTTP aislada con TestClient/base temporal y correo simulado: respuesta uniforme, reset correcto, segundo uso 400, token invalido/expirado 400, token de recuperacion rechazado como sesion, sesion rechazada como recuperacion, cambio sin sesion 401, contrasena actual incorrecta 400 y login con nueva contrasena correcto. Payload Resend con HTML/texto verificado mediante transporte simulado. Comprobacion adicional: token vacio 400 y texto uniforme exacto.
- Frontend: `npm --prefix uis/backoffice run build` y `npm --prefix uis/backoffice run lint`: correctos. Diagnosticos del editor sin errores en los archivos nuevos y modelos/rutas tocados. No se crearon archivos nuevos de pruebas ni se ejecuto la suite completa.
- Entorno: se instalaron `uv`, dependencias Python/frontend y Chromium para poder verificar. Primera compilacion bloqueada por dependencias ausentes; correcta tras `npm install`.
- Bloqueos: Chromium no arranca por falta de `libatk-1.0.so.0`; no se ejecutaron las interacciones, capturas ni la comprobacion responsive visual. Falta entrega real de correo con clave/remitente Resend validos y URL publica configurada. PageSpeed no medido sin URL publica. No se accedio a secretos ni se cambiaron rutas protegidas fuera del progreso y del nuevo archivo historico autorizado.
- Frontend local iniciado en `http://localhost:5174`; requiere la API FastAPI en 8081 y configuracion de correo para el flujo real. No se creo commit.
- Contexto activo archivado integro en `memory-bank/history/AUTH03-CONTEXT.md`; se conserva el contexto raiz modificado por el usuario y el historico AUTH-01.
- Siguiente paso: configurar variables privadamente, iniciar FastAPI, instalar dependencias Linux de Chromium con permisos adecuados y validar recepcion real del correo, flujo de navegador y responsive. Rate limiting y auditoria opcionales no implementados.

## Estado general
- Actualizacion 2026-10-15: implementado AUTH-01 (Hito 10) sobre la API FastAPI/TinyDB; suite backend completa con 35 pruebas correctas.
- El `CONTEXT.md` AUTH-01 activo se archivo integro en `memory-bank/history/HITO10-CONTEXT.md`; no se modifico el contexto de raiz.
- AUTH-01 implementa JWT stateless, hash bcrypt con libpass, usuarios y perfiles en TinyDB, y protege las seis rutas de proveedores.
- La verificacion automatica cubre `/docs`/OpenAPI, registro-login-Bearer, 401/403 y JWT expirado. La secuencia visual manual en Swagger aun no se ejecuto.
- La clave real no se crea ni se versiona en este cambio; `services/api/README.md` documenta generacion local de `.env`, excluido de Git.
- Actualizacion 2026-10-08: implementado el Hito 09, directorio de proveedores de TrackFlow, conforme al contexto archivado y la rubrica de entrega.
- Backend y frontend funcionales; backend, persistencia real, integracion HTTP, build y lint verificados. Verificacion visual de navegador pendiente por bibliotecas del contenedor.
- Contexto del Hito 09 archivado integro en `memory-bank/history/HITO09-CONTEXT.md`; el original de raiz se conserva.
- Se mantiene el analizador de incidencias anterior, cuyo contexto esta archivado en `memory-bank/history/INCIDENTS-ANALYSIS-CONTEXT.md`.
- Regla permanente de archivo y progreso incorporada en `AGENTS.md`, independiente de commits, con autorizacion del desarrollador para estas actualizaciones.

## Protocolo para nuevas funciones
- Antes de implementar o anadir cualquier funcion, leer este `progress.md` y revisar los contextos de `memory-bank/history/` para conocer el estado, las decisiones y las funciones existentes.
- Contrastar esos antecedentes con el `CONTEXT.md` activo; no inventar requisitos ni duplicar implementaciones existentes.
- Al cerrar cada hito, archivar una copia integra del contexto en `memory-bank/history/` y actualizar este archivo, incluso sin commit. Preservar historicos existentes y registrar expresamente pruebas pendientes o bloqueadas.

## Completado
- Hito 10 - AUTH-01:
  - Modelos `User` con email, contraseña hasheada, estado, rol enum y fecha; `Profile` independiente, 1:1 por `user_id`. El modelo User no incluye nombre ni contacto.
  - Servicios TinyDB para crear/buscar/listar/actualizar/eliminar usuarios y leer/actualizar perfiles; el registro crea el perfil asociado y la eliminación lo borra.
  - `/users` ofrece registro público con rol fijo `user`; CRUD protegido con reglas de dueño/admin y modificación de roles limitada a admin.
  - `/auth/login` emite JWT firmado, con expiración `ACCESS_TOKEN_EXPIRE_MINUTES`; `/auth/me` incluye perfil. Bcrypt usa `libpass[bcrypt]` y `from passlib.hash import bcrypt`.
  - `/profiles/me` solo opera sobre el perfil propio. `get_current_user` valida Bearer, firma, expiración y existencia/estado del usuario; tokens ausentes, inválidos y expirados reciben 401, recursos ajenos 403.
  - Los seis endpoints existentes de `/suppliers` ahora requieren JWT. OAuth2 bearer y HTTP bearer están publicados en OpenAPI para usar `/docs` y pegar el token.
  - Clave tomada desde `services/api/.env`, nunca definida en código; el archivo se ignora en Git. `README.md` documenta crearla con `openssl rand` y ejecutar el servicio.
  - Se archivó el contexto activo completo como `memory-bank/history/HITO10-CONTEXT.md`; `AGENTS.md` explicita que debe hacerse incluso si se indica que el contexto ya está actualizado.
- Hito 09 - directorio de proveedores:
  - Estructura de entrega: `services/api/main.py`, `models.py`, `database.py`, `routes/suppliers.py` y `seed.py`.
  - Modelos Pydantic separados de entrada/respuesta con los campos exactos, categorias del contexto, estados `active`/`suspended`, tarifa positiva y finita, moneda por pais y `updated_at` UTC generado por el sistema.
  - CRUD `/suppliers`, filtros combinados por pais/categoria, actualizacion de tarifa con timestamp y errores 404/422. ID generado por TinyDB.
  - Seeder ejecutable con `uv run seed`: 15 proveedores exactos, comprobacion previa por nombre/pais, sin duplicados ni sobrescritura de tarifas modificadas; informa del numero de inserciones.
  - Persistencia TinyDB compartida por API y seeder; bloqueo dentro del proceso, una instancia/worker y seeder con API detenida.
  - API Node existente conserva incidencias y redirige `/suppliers` a FastAPI; arranque conjunto con `npm run dev`.
  - Pagina en `uis/backoffice/src/app/suppliers/SupplierDirectory.tsx`, accesible desde el menu: listado API, filtros sin recarga, formulario completo con errores, tarifas editables y controles de suspension/reactivacion con estados diferenciados.
  - Se mantiene `uis/backoffice` como aplicacion interna React/Vite; no se duplica en `uis/application` ni se migra a Next.js.
  - Comandos de instalacion, seeder, ejecucion y pruebas documentados en los README de API y backoffice.
  - Contexto archivado y regla permanente actualizada por peticion explicita del desarrollador.
- Fase 1 - analisis CSV local:
  - Creada la CLI `scripts/analyze.py` y el modulo reutilizable `scripts/incidents_analysis.py` con validacion del esquema, clasificacion de errores, metricas, CSAT y exportacion a `results.csv`.
  - El archivo de referencia `incidents-trackflow.csv` se compara con los resultados declarados en `CONTEXT.md`; las discrepancias detienen el proceso.
  - Validado mediante fixture sintetico anonimizado con los conteos de referencia: 100 filas, 95 validas, 5 invalidas y CSAT 3.06. La exportacion se comprobo sin correos.
  - Confirmado que `satisfaction_score` puede omitirse del encabezado; los registros `CLOSED` sin puntuacion se clasifican como invalidos.
  - Validacion final con el CSV real: coincidencia exacta con totales, categorias, estados, paises, cinco reglas de error y CSAT declarados en `CONTEXT.md`.
- Fase 2 - integracion en plataforma:
  - La API expone `POST /api/incidents/analyze` (multipart/form-data) y `GET /api/incidents/results/export`, reutiliza el procesador Python y responde con errores descriptivos.
  - El backoffice ofrece la vista navegable de analisis, carga por selector/drag-and-drop, metricas, desgloses, errores de validacion y descarga CSV.
  - Documentados los comandos y configurado proxy `/api` de Vite hacia el servicio local.
  - Prueba web end-to-end con el CSV real: reporte y descarga correctos; la respuesta y el CSV no contienen emails.
- Analisis del contexto TrackFlow (operacion en Los Angeles y Zaragoza, retos de inventario, carrier management, devoluciones y CX).
- Revision de estructura actual del monorepo y convenciones de carpetas `uis/` y `services/`.
- Creacion de `memory-bank/` con:
  - `projectbrief.md`
  - `techContext.md`
  - `progress.md` (este archivo)
- Creacion de `AGENTS.md` con:
  - Archivos obligatorios de lectura al inicio de sesion.
  - Flujo obligatorio pre-commit (revision, validaciones, reglas, actualizacion de progreso y commit).
  - Rutas protegidas que no deben tocarse sin confirmacion explicita.
- Creacion de `.agents/rules/component-naming-convention.md` con alcance siempre activo para `uis/website/**` y `uis/backoffice/**`.
- Creacion de skill reusable en `.agents/skills/create-reusable-ui-component/SKILL.md` con objetivo, inputs y criterios verificables.
- Inicializacion y personalizacion de `uis/website`:
  - React + TypeScript + Vite + Tailwind.
  - Web corporativa completa en `/` (mision, vision, servicios, equipo, contacto).
  - Formulario funcional con validaciones y boton de limpiar.
  - Metadatos SEO y JSON-LD en `index.html`.
- Inicializacion y personalizacion de `uis/backoffice`:
  - Layout interno independiente.
  - Vista de entrada con KPIs y alertas operativas visibles en pantalla.
- Creacion de `services/api` con endpoint `GET /health` y README operativo.

## En progreso
- Hito 10: pendiente abrir `/docs` en navegador y ejecutar allí el registro-login-Authorize manual; el flujo HTTP, los esquemas OpenAPI, errores 401/403 y expiración ya están cubiertos por pruebas.
- Hito 09: pendiente completar la verificacion visual/funcional en Chromium (desktop y movil) tras instalar las dependencias de sistema del navegador.

## Validaciones ejecutadas
- Hito 10: `cd services/api && uv run pytest -q`: 35 pruebas correctas; una advertencia preexistente de Starlette/httpx.
- Hito 10: `uv run pytest -q tests/test_auth.py`: 6 pruebas correctas, incluyendo registro, hash bcrypt, login JSON/formulario OAuth2, roles, permisos, perfil, JWT inválido/expirado y esquema OpenAPI.
- Hito 10: persistencia de proveedores validada con autorización Bearer a través de reinicio real de Uvicorn.
- Hito 10: compilación Python y registro de rutas en OpenAPI validados; import de `jose.jwt`, `dotenv.load_dotenv` y `passlib.hash.bcrypt` validado.
- Hito 09: `cd services/api && uv run pytest -q --tb=short`: 29 pruebas correctas; incluye comparacion literal de seeder/categorias/estados con `CONTEXT.md` y reinicio de un proceso Uvicorn real con recuperacion de los datos.
- Hito 09: `uv run seed` dos veces sobre base temporal nueva: `Proveedores insertados: 15` y `Proveedores insertados: 0`. Base existente: 0 y 0.
- Hito 09: `cd uis/backoffice && npm run test:e2e -- --grep 'API integrada'`: 1 prueba correcta a traves de Vite/Node/FastAPI, con validaciones, CRUD y conservacion del endpoint de incidencias.
- Hito 09: `npm run build` y `npm run lint` en `uis/backoffice`: correctos despues de reorganizar la pagina.
- Hito 09: prueba visual Playwright preparada (filtros, alta, validacion cliente, errores API, tarifas, estados, desktop/movil); no se considera aprobada porque Chromium no inicia sin `libatk-1.0.so.0`.
- `npm run typecheck` en raiz: OK (tras instalar dependencias raiz con `npm install`).
- `npm run build` en `uis/website`: OK.
- `npm run build` en `uis/backoffice`: OK.
- Smoke test backend: `GET /health` responde `status: ok`.
- `python3 scripts/analyze.py` con fixture sintetico anonimo: coincidencia de referencias (100 total, 95 validos, 5 invalidos, CSAT 3.06); exportacion sin correos.
- `python3 scripts/analyze.py scripts/incidents-trackflow.csv`: OK, resultados coinciden con el contexto; filas invalidas 4, 26, 43, 69 y 98, sin exposicion de emails.
- Prueba API multipart y exportacion: OK; CSV vacio devuelve 422 y extension incorrecta 400.
- Prueba end-to-end a traves de Vite con CSV real: carga, metricas y descarga OK; respuesta y CSV sin emails.
- `npm --prefix uis/backoffice run build`: OK.

## Proximos pasos
- Hito 10: generar `services/api/.env` siguiendo el comando de `services/api/README.md` y completar la comprobacion manual visual de `/docs`.
- Instalar bibliotecas Linux de Chromium con `npx playwright install-deps chromium` desde una terminal con permisos adecuados y ejecutar `npm run test:e2e` en el backoffice con ambos servicios activos.
- Revisar por separado la vulnerabilidad alta preexistente de `source-map-js@1.2.1`, detectada por npm audit; no se ha aplicado una actualizacion ajena al alcance.

## Riesgos / bloqueos conocidos
- Aun no existe pipeline CI del monorepo para validar todos los subproyectos de forma unificada.
- Captura automatica con Playwright bloqueada en este entorno por dependencia de sistema faltante (`libatk-1.0.so.0`).
- TinyDB requiere una unica instancia/worker; no admite escrituras multiproceso seguras ni seeder concurrente con la API.
- Dependencia preexistente `source-map-js@1.2.1` con aviso de seguridad alto. Starlette TestClient muestra una advertencia de deprecacion de httpx, sin fallos de pruebas.
