AUTH-02 — Flujos de autenticación y vistas protegidas en el frontend
La API ya exige un token JWT en las rutas protegidas. Esta tarea cubre el lado frontend de ese contrato:

Vistas de login y registro — formularios que llaman a la API, reciben el token y lo almacenan correctamente.
Vistas de gestión de cuenta — página de perfil.
Protección de rutas — cualquier vista que requiera sesión debe redirigir a los usuarios no autenticados al login. Esto aplica a todas las aplicaciones del monorepo excepto el website público (Hito 1), que permanece completamente público.
El token debe almacenarse en localStorage y adjuntarse a cada llamada protegida a la API mediante la cabecera Authorization: Bearer. Al cerrar sesión, el token se elimina y el usuario es redirigido al login.

No construyas una aplicación de autenticación separada. Integra estos flujos en las aplicaciones Next.js existentes dentro de tu monorepo.

Conocimiento complementario: el lado frontend del JWT
Una vez que la API devuelve un token en el login, el trabajo del frontend es: almacenarlo, enviarlo y reaccionar a su ausencia. El patrón estándar en Next.js es:

Almacenar el token en localStorage después de una respuesta de login exitosa.
Leer el token en cada llamada protegida a la API y establecerlo en la cabecera Authorization: Bearer <token>.
Proteger rutas — usa un layout guard o hook en el cliente que lea localStorage y redirija a /login si no hay token. El middleware de Next.js corre en el servidor y no puede leer localStorage; no lo uses para esta comprobación salvo que también guardes una cookie que el middleware pueda ver.
Limpiar el token al cerrar sesión y redirigir.