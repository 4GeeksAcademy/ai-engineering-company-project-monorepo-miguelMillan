# Plan de pruebas

Este plan cubre las decisiones de negocio de la autenticación existente. Las pruebas pueden usar TestClient para invocar la aplicación, pero deben afirmar decisiones, cambios de estado y permisos; no deben probar serialización HTTP, OpenAPI ni detalles internos de FastAPI. Los correos de recuperación se simulan: ninguna prueba contacta Resend.

## Ejecución

Desde la raíz del monorepo:

```bash
uv run --directory services/api pytest
uv run --directory services/api pytest --cov=routes.auth --cov-report=term-missing --cov-fail-under=70
uv run --directory services/api pytest tests/test_suppliers.py --cov=routes.suppliers --cov-report=term-missing --cov-fail-under=60
uv run --directory services/api pytest tests/test_incidents.py --cov=routes.incidents --cov-report=term-missing --cov-fail-under=60
npm --prefix uis/backoffice test -- --coverage
```

Los dos comandos con pytest-cov miden cada grupo de endpoints de forma independiente y exigen al menos 60% en su módulo. La suite de autenticación conserva su umbral del 70%. Jest cubre helpers de sesión y utilidades TypeScript. Para ejecutar la suite frontend desde su propio proyecto: `cd uis/backoffice && npm test -- --coverage`. Las suites API usan bases temporales y no dependen de servicios externos.

## Resultado de validación

- FastAPI: 66 pruebas correctas; `routes/auth.py` con 98,11% de cobertura.
- Proveedores: 29 pruebas correctas; `routes/suppliers.py` con 100% de cobertura.
- Incidencias: 4 pruebas correctas; `routes/incidents.py` con 100% de cobertura.
- Jest: 14 pruebas en 2 suites; `session.ts`, `supplierUtils.ts` e `incidentErrors.ts` con 100% de cobertura.
- Backoffice: build y lint correctos. Pytest conserva una advertencia no bloqueante de deprecación de Starlette/httpx.

## Suites y casos previstos

Los tests de API viven en `services/api/tests/`, raíz del proyecto FastAPI. Cada endpoint tiene pruebas de camino feliz, límite y fallo. Los casos se eligen para cubrir validación, autorización, efectos de negocio e idempotencia sin comprobar el formato de serialización.

### Registro — `POST /users`

- **Camino feliz:** registra una cuenta, aplica rol de usuario, guarda una contraseña hasheada y crea el perfil con los campos opcionales proporcionados. Comprueba el estado persistido, no el esquema de respuesta.
- **Límite:** acepta contraseña con longitud mínima y perfil opcional omitido.
- **Fallo:** email inválido, contraseña demasiado corta, rol/campo adicional prohibido y email duplicado; comprobar que no se crea una segunda cuenta.

### Inicio de sesión — `POST /auth/login`

- **Camino feliz:** credenciales válidas en JSON y formato de formulario compatible con Swagger; el token emitido permite acceder a una ruta protegida.
- **Límite:** rechaza cuerpo vacío o campos requeridos ausentes sin autenticar.
- **Fallo:** contraseña incorrecta, cuenta desconocida o inactiva y cuerpo JSON/formulario malformado.

### Usuario autenticado — `GET /auth/me`

- **Camino feliz:** devuelve el usuario y el perfil vinculado para una sesión vigente.
- **Límite:** una cuenta sin perfil vinculado sigue siendo autenticable y devuelve perfil ausente.
- **Fallo:** token ausente, malformado, expirado, de propósito incorrecto o asociado a usuario inactivo.

### Solicitud de recuperación — `POST /auth/forgot-password`

- **Camino feliz:** para una cuenta existente, programa la recuperación y simula el envío del enlace sin llamar al proveedor externo.
- **Límite:** email no registrado e email registrado reciben la misma confirmación, evitando enumeración de cuentas.
- **Fallo:** email inválido o campo requerido ausente; no se programa una tarea de recuperación.

### Restablecimiento — `POST /auth/reset-password`

- **Camino feliz:** token emitido y vigente cambia la contraseña; la nueva clave permite iniciar sesión.
- **Límite:** acepta contraseña de longitud mínima y un token solo puede utilizarse una vez.
- **Fallo:** token inválido, expirado, reutilizado o de propósito incorrecto; contraseña fuera de límites.

### Cambio de contraseña — `POST /auth/change-password`

- **Camino feliz:** sesión vigente y contraseña actual correcta permiten cambiar la clave y autenticarse con la nueva.
- **Límite:** acepta contraseña nueva con longitud mínima.
- **Fallo:** sesión ausente, malformada o expirada; contraseña actual incorrecta y cuerpo inválido. La contraseña anterior deja de autenticar tras un cambio exitoso.

### Validación de tokens — helper de autenticación

No existe un endpoint independiente de emisión o validación de tokens: la emisión ocurre en login y la validación se ejerce mediante `GET /auth/me` y el endpoint protegido de cambio de contraseña.

- **Camino feliz:** token vigente y con propósito `access` identifica al usuario activo.
- **Límite:** expiración y claims requeridos se verifican en el borde temporal configurado.
- **Fallo:** firma inválida, expiración pasada, `sub` inválido, propósito de recuperación usado como sesión y usuario inactivo.

### Helpers TypeScript de sesión — Jest

- **Camino feliz:** guardar un token lo hace disponible para lectura; eliminarlo limpia la sesión.
- **Límite:** leer o limpiar almacenamiento vacío es seguro.
- **Fallo:** comprobar comportamiento ante fallo del almacenamiento del navegador solo si el helper define una recuperación; no inventar un contrato distinto al código actual.

### Proveedores — grupo `/suppliers`

- **Camino feliz:** crear un proveedor, consultarlo, filtrarlo, cambiar tarifa/estado y eliminarlo; verificar persistencia y reglas de negocio.
- **Límite:** lista vacía y filtros válidos sin coincidencias; tarifa positiva mínima aceptada.
- **Fallo:** tarifa/categoría/país no válidos, recurso inexistente, filtros fuera de catálogo y acceso sin sesión.

### Incidencias — grupo `/api/incidents`

- **Camino feliz:** crear, listar, consultar detalle y resumen; avanzar por una transición permitida y comprobar el estado persistido.
- **Límite:** lista y resumen vacíos, filtros válidos sin coincidencias y cambio desde `open` a `in_progress`.
- **Fallo:** campos o UUID inválidos, recurso inexistente, transición no permitida y acceso sin sesión.

### Helpers de frontend — Jest

- **`positiveRate`:** acepta tarifa finita positiva; rechaza cero, negativos, `NaN` e infinito.
- **`errorMessage`:** devuelve mensaje de un `Error`; usa fallback para valores que no son errores.
- **`incidentError`:** conserva el mensaje de `IncidentApiError`; usa mensaje seguro para errores desconocidos.
- **Sesión:** mantiene los casos existentes de guardar, leer y limpiar token, incluida la sesión vacía.

## Cobertura de suites

- **Caso identificado con ayuda de IA:** se añadieron pruebas para impedir que un token de recuperación se acepte como token de sesión y para rechazar la reutilización de un enlace de reset. Están cubiertas en `test_token.py` y `test_reset_password.py`.
- `test_register.py`: reglas de registro, duplicados, hash y creación del perfil asociado.
- `test_login.py`: aceptación de credenciales/formato de entrada y rechazo de credenciales o cuerpos no válidos.
- `test_auth_me.py` y `test_token.py`: identidad, perfil y decisiones de autorización/validación del token.
- `test_forgot_password.py`, `test_reset_password.py` y `test_change_password.py`: privacidad de la solicitud, emisión/consumo de enlace, cambios de credenciales y fallos relacionados.
- `test_suppliers.py` y `test_incidents.py`: ciclo de negocio de proveedores e incidencias con filtros, límites, transiciones y fallos.
- `src/__tests__/auth-session.test.ts` y `src/__tests__/frontend-utilities.test.ts`: helpers de sesión, validación de tarifas y traducción de errores del frontend.

El código de producción no forma parte del alcance salvo que una prueba revele un defecto directamente relacionado con autenticación. Si ocurre, documentar el fallo y su corrección aquí antes de ampliar el alcance.
