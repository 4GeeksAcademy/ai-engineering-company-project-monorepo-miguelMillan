# Propuesta de Arquitectura Backend: TrackFlow

## 1. Contexto del negocio y justificacion del patron arquitectonico

### Operacion que debe soportar

TrackFlow opera fulfillment y ultima milla para marcas de e-commerce en Estados Unidos y Espana, con almacenes en Los Angeles y Zaragoza. La plataforma debe crear una vista operativa comun sobre sistemas de almacen hoy diferentes y cubrir el recorrido de una unidad: inventario por SKU y almacen, entrada o salida de stock, pedido, asignacion de carrier, seguimiento, entrega o incidencia y, cuando proceda, devolucion.

Los flujos con prioridad inicial son los que ya sostienen los hitos 1 a 4:

- Consultar inventario por SKU, ubicacion y umbral de stock para eliminar la falta de visibilidad entre ambos almacenes.
- Ingerir y gestionar envios con origen, destino, prioridad, valor declarado y estado; los operadores necesitan asignar un carrier apto para cada envio.
- Aplicar el criterio existente de seleccion de carrier: pais de destino, peso total, prioridad, fragilidad, fiabilidad y coste. Esto da soporte a los ocho carriers de Estados Unidos y Espana.
- Registrar movimientos de inventario y eventos de envio de forma trazable para alimentar alertas, el backoffice y los indicadores ejecutivos.
- Preparar los limites para devoluciones basadas en reglas por cliente, consulta de tracking para destinatarios, tickets de CX y reportes comerciales. Estos dominios no deben condicionar la primera entrega de inventario y envios, pero comparten entidades y auditoria.

Los usuarios tienen necesidades distintas sobre la misma informacion: operativos de almacen y coordinadores de transporte actualizan el estado; el equipo de CX consulta tracking y devoluciones; gestores de cuenta y direccion consumen agregados; las marcas y destinatarios solo acceden a sus recursos autorizados. La arquitectura debe conservar reglas coherentes entre paises y dejar trazabilidad para explicar una asignacion de carrier o un cambio de stock.

### Patron seleccionado: monolito modular por capas

Se propone un **monolito modular por capas** implementado con FastAPI. Un unico servicio desplegable contiene modulos de dominio independientes y cada modulo separa API, validacion, logica de aplicacion y persistencia. El despliegue inicial y la observabilidad quedan centralizados, mientras que las fronteras internas permiten extraer una integracion o un dominio cuando el volumen, la disponibilidad o el ciclo de cambios lo justifiquen.

Este patron encaja con TrackFlow porque los flujos iniciales son transaccionales y estan estrechamente relacionados: asignar un envio necesita producto, cantidad, almacen, destino y capacidades del carrier; confirmar su salida afecta al inventario y debe generar una traza. Mantenerlos en el mismo proceso reduce integraciones internas prematuras para un equipo tecnico de siete personas, al tiempo que los modulos evitan reproducir el actual conjunto de scripts punto a punto y reglas dispersas.

### Alternativas descartadas

**MVC clasico.** Es valido para aplicaciones CRUD sencillas, pero tenderia a concentrar reglas como el scoring de carriers, las transiciones de envio y las politicas de devolucion en controllers o modelos. TrackFlow necesita que esas reglas evolucionen, se prueben y sean reutilizables desde API, procesos de ingesta y tareas programadas. Las capas de aplicacion y CRUD propuestas hacen esa responsabilidad explicita.

**Serverless como arquitectura principal.** Los webhooks de carriers, notificaciones o reportes semanales podrian evolucionar a ejecuciones eventuales, pero no resuelve la necesidad inmediata de una API coherente para dos almacenes, estados consistentes y observabilidad centralizada. Multiplicaria configuraciones, permisos y trazas distribuidas antes de disponer de contratos e integraciones estables. Se reserva como opcion para consumidores asincronos concretos tras estabilizar los modulos.

**Microservicios.** Tampoco se proponen en la primera fase: inventario, envio y asignacion de carrier comparten datos y cambios coordinados. Separarlos ahora trasladaria la inconsistencia actual a llamadas de red, colas y despliegues adicionales. El monolito modular conserva limites que permiten una extraccion posterior basada en evidencia, por ejemplo un adaptador de tracking o un motor de devoluciones de alta carga.

## 2. Estructura de carpetas y organizacion por modulos

El backend se alojara en `services/api` y sustituira progresivamente el servidor de salud actual por una aplicacion FastAPI. La siguiente estructura es una propuesta de destino; no implica implementar todos los modulos en la primera iteracion.

```text
services/api/
  app/
    main.py
    api/
      v1/
        router.py
        inventory.py
        shipments.py
        carriers.py
        tracking.py
        returns.py
        clients.py
        cx.py
        reporting.py
        health.py
    core/
      config.py
      security.py
      logging.py
      exceptions.py
    crud/
      products.py
      inventory_movements.py
      shipments.py
      carriers.py
      returns.py
      clients.py
      tickets.py
    db/
      base.py
      session.py
      migrations/
    models/
      product.py
      inventory_movement.py
      shipment.py
      carrier.py
      return_request.py
      client.py
      ticket.py
    schemas/
      product.py
      inventory.py
      shipment.py
      carrier.py
      tracking.py
      return_request.py
      client.py
      ticket.py
      report.py
    services/
      carrier_selection.py
      tracking_aggregation.py
      return_policy.py
      reporting.py
    integrations/
      carriers/
      wms/
      crm/
    tests/
      api/
      services/
      crud/
  pyproject.toml
  README.md
```

La separacion principal es por responsabilidad y dominio, no por pantalla. `app/api` contiene routers y dependencias HTTP; no decide reglas de negocio ni accede directamente a la base de datos. `app/core` concentra configuracion, seguridad, manejo de errores y observabilidad transversales. `app/crud` es la unica capa responsable de las operaciones de persistencia por agregado; permite cambiar el ORM o la estrategia de consulta sin alterar contratos HTTP.

`app/models` define modelos ORM y relaciones persistentes: representan tablas, claves, indices y estados almacenados. `app/schemas` define contratos Pydantic de entrada, salida y filtros: representan lo que un consumidor puede enviar o recibir, incluidas validaciones y campos expuestos. `app/db` administra el motor, sesiones y migraciones. `app/services` contiene los casos de uso que coordinan varios repositorios o integraciones, como seleccionar el mejor carrier y registrar la asignacion. `app/integrations` encapsula las diferencias entre los WMS de Los Angeles/Zaragoza y las APIs de los carriers, para que el dominio no dependa de un proveedor concreto.

### Convenciones de FastAPI que fundamentan la estructura

La estructura sigue convenciones consolidadas de proyectos FastAPI: routers pequeños registrados desde un router versionado, dependencias compartidas para sesion y autenticacion, configuracion tipada central y modulos de esquema separados por recurso. FastAPI usa Pydantic para deserializar, validar y documentar contratos HTTP, mientras que el ORM representa persistencia; por eso `schemas` y `models` no se mezclan aunque inicialmente tengan campos parecidos.

Esta distincion es esencial en TrackFlow. El modelo `Shipment` puede guardar referencias internas, auditoria y datos de integracion, mientras que un esquema de creacion acepta solamente SKU, cantidad, origen, destino, prioridad y valor declarado. Un esquema de respuesta puede publicar identificador, estado, carrier y timestamps, sin filtrar detalles operativos a un destinatario. De igual forma, las reglas de peso, dimensiones, cantidades y tarifas se validan en schemas antes del caso de uso, y las invariantes de negocio se vuelven a comprobar en servicios cuando intervienen varios registros.

## 3. Organizacion de endpoints, routers y dominios

Todos los recursos de negocio se publicaran bajo `/api/v1`. `app/api/v1/router.py` los agrupa y `main.py` registra ese router junto al endpoint operativo no versionado `GET /health`. Cada router usa `APIRouter`, etiquetas de documentacion y dependencias de autorizacion apropiadas. No se define aqui implementacion ni formato definitivo de cada payload.

| Router | Prefijo principal | Responsabilidad de dominio |
| --- | --- | --- |
| Inventario | `/api/v1/inventory` | Disponibilidad por SKU y almacen, alertas de bajo stock y movimientos de entrada, salida, transferencia o ajuste. |
| Productos | `/api/v1/products` | Catalogo operativo, dimensiones, fragilidad, umbrales y estado del SKU. |
| Envios | `/api/v1/shipments` | Alta y consulta de envios, transiciones de estado, asignacion de carrier e incidencias. |
| Carriers | `/api/v1/carriers` | Capacidades, cobertura por pais, tarifas, prioridades admitidas y metricas de rendimiento de los ocho partners. |
| Tracking | `/api/v1/tracking` | Consulta unificada de eventos de envio; delega en adaptadores de carrier y ofrece la superficie para el portal publico. |
| Devoluciones | `/api/v1/returns` | Solicitudes, aprobacion segun reglas por cliente, recogida, etiqueta, inspeccion y resolucion. |
| Clientes | `/api/v1/clients` | Perfil de marca, contratos, reglas operativas y datos necesarios para reportes o riesgo de renovacion. |
| CX | `/api/v1/tickets` | Tickets y sus referencias a envio o devolucion; separa la operacion de soporte de las consultas de tracking. |
| Reportes | `/api/v1/reports` | Agregados de inventario, envios, puntualidad, coste, devoluciones y CX para backoffice, clientes y direccion. |
| Salud | `/health` | Liveness/readiness del servicio, sin datos de negocio ni autenticacion de usuario final. |

La seleccion de carrier no sera un router aislado: pertenece al caso de uso de envios porque se ejecuta al crear o asignar un envio. Su explicacion de puntuacion y coste debe quedar registrada junto con la asignacion para que los coordinadores puedan auditarla. Tracking, aunque se apoya en envios, se separa porque orquesta proveedores externos y tiene requisitos distintos de disponibilidad y exposicion publica.

## 4. Arquitectura desacoplada: frontend y backend separados

### Estrategia de repositorio

Se mantiene el monorepo existente. `uis/website` conserva la web corporativa y el futuro portal publico de tracking; `uis/backoffice` conserva la interfaz interna de operaciones; `services/api` contiene el backend FastAPI; `packages/shared` puede alojar contratos agnosticos o clientes generados cuando exista una necesidad real. Esta disposicion permite revisar en una sola solicitud cambios coordinados de contrato, cliente y servicio, sin obligar a desplegar las dos interfaces juntas.

El desacoplamiento se consigue por contratos de red, no por ubicar el codigo en repositorios distintos. Cada interfaz se compila y despliega de forma independiente, no importa internamente codigo de `services/api` y trata la API versionada como unica fuente de datos operativos. El backend no conoce componentes ni rutas de Vite/React; solo autentica, autoriza y responde recursos JSON.

### Comunicacion y versionado

La comunicacion sera REST sobre HTTPS y JSON. El prefijo `/api/v1` fija un contrato estable para website y backoffice. Cambios compatibles se incorporan como campos o endpoints nuevos dentro de `v1`; un cambio incompatible requiere una nueva version, por ejemplo `/api/v2`, con convivencia y plan de retirada. Los estados de envio, ubicaciones de almacen y prioridades deben definirse como valores de dominio estables y documentados para que las UI no dependan de etiquetas de proveedor.

Las integraciones de WMS, ERP, CRM y carriers quedan detras de adaptadores del backend. Ninguna UI debe llamar directamente a los ocho carriers ni a los sistemas de cada almacen: asi se evita duplicar credenciales, normalizacion de estados y tratamiento de errores entre clientes.

### Variables de entorno y CORS

`app/core/config.py` cargara la configuracion desde variables de entorno y un archivo `.env` local mediante `pydantic-settings`. Debe tipar, como minimo, entorno, URL de base de datos, origenes CORS permitidos, configuracion de telemetria y credenciales o endpoints de integraciones. Los secretos nunca se exponen al frontend ni se versionan.

`CORSMiddleware` recibira una lista explicita por entorno de los origenes desplegados de `uis/website` y `uis/backoffice`, junto con los origenes locales necesarios. No se utilizara `*` cuando se envien credenciales o tokens. Metodos, cabeceras y credenciales se limitaran a lo que use cada cliente; el portal publico de tracking tendra permisos distintos de las operaciones autenticadas del backoffice.

## 5. Analisis de riesgos y puntos de atencion

| Riesgo | Consecuencia concreta en TrackFlow | Control arquitectonico |
| --- | --- | --- |
| Logica de negocio dentro de handlers o routers | La formula de coste o scoring podria acabar duplicada entre alta de envios, reasignaciones e integraciones. Un cambio de tarifa o prioridad produciria decisiones distintas entre Los Angeles y Zaragoza. | Routers finos; los casos de uso viven en `services`, con pruebas unitarias para seleccion de carrier, transiciones y reglas de devolucion. |
| Mezclar modelos ORM y schemas Pydantic | Un endpoint podria exponer campos internos de contratos o integraciones, aceptar estados que solo debe establecer el sistema o acoplar una migracion de base de datos a las UI. | Modelos ORM solo en `models`; contratos separados en `schemas` por accion y audiencia, con listas explicitas de campos de lectura y escritura. |
| Integraciones de proveedores filtradas al dominio | Las diferencias entre los dos WMS o los formatos de ocho carriers contaminarian envios y tracking, haciendo dificil probar o reemplazar un proveedor. | Adaptadores en `integrations` que normalicen eventos y errores hacia contratos de dominio estables. |
| Falta de trazabilidad y observabilidad central | El equipo seguiria detectando fallos mediante mensajes manuales y no podria explicar discrepancias de stock, incidencias o decisiones de asignacion. | Logging estructurado, identificadores de correlacion, eventos/auditoria por movimiento y asignacion, y metricas/alertas configuradas desde `core`. |
| Control de acceso insuficiente entre B2B, B2C e interno | Un destinatario podria consultar un envio ajeno o una marca acceder a datos de otra cuenta; el backoffice podria operar sin segmentacion por rol. | Dependencias de autenticacion y autorizacion centralizadas en `core/security`, con comprobacion de tenant/cliente y roles en cada caso de uso. |

## Decision y siguiente paso

La primera iteracion debe implementar la base FastAPI, configuracion, sesion de base de datos, router versionado y los modulos de productos, inventario, envios y carriers. Debe incluir contratos Pydantic separados, persistencia, pruebas de los casos de uso de asignacion y trazabilidad de movimientos. Tracking, devoluciones, CX, CRM y reporting se incorporaran como modulos posteriores sobre estas fronteras, sin cambiar el contrato ni el despliegue de las interfaces ya existentes.