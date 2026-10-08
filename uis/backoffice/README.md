# TrackFlow Backoffice

Aplicacion interna para operacion y seguimiento de KPIs de TrackFlow.

## Stack
- React + TypeScript + Vite
- Tailwind CSS v4 (via `@tailwindcss/vite`)

## Ejecutar en local
```bash
cd uis/backoffice
npm install
npm run dev
```

La app queda disponible por defecto en `http://0.0.0.0:5174` para compatibilidad con Codespaces.

La vista **Análisis de incidencias** usa el proxy `/api` de Vite, dirigido por defecto a `http://localhost:8080`. Configura `API_PROXY_TARGET` si el backend local usa otra URL. Para desplegar el frontend por separado, configura `VITE_API_URL` con la URL pública de la API.

## Directorio de proveedores
La pagina esta organizada en `src/app/suppliers/SupplierDirectory.tsx`. Se mantiene la aplicacion existente en `uis/backoffice` segun el alcance y la rubrica: React/Vite, sin crear una segunda aplicacion en `uis/application`. El formulario y las filas reutilizables se encuentran en `src/components`.

La opcion **Proveedores** del menu muestra el directorio centralizado de USA y Spain: categorias, tarifa por envio o unidad en USD/EUR, ultima actualizacion y estado. Incluye filtros combinados sin recarga, alta con todos los campos del contexto, edicion de tarifa y controles para suspender/reactivar. Las respuestas rechazadas por la API se muestran como errores accesibles; los cambios se reflejan al finalizar la peticion.

Inicia primero el backend en otra terminal:
```bash
cd services/api
uv sync
uv run seed
npm run dev
```

Vite redirige `/suppliers` a la misma API Node (8080), que conserva las rutas de incidencias y se comunica con FastAPI (8081). Para usar otros puertos, ejecuta por ejemplo `API_PROXY_TARGET=http://localhost:8082 npm run dev -- --host 0.0.0.0 --port 5175`; el backend correspondiente debe estar iniciado. En Codespaces basta publicar el puerto del frontend para este flujo por proxy.

## Build
```bash
npm run build
```

## Pruebas del directorio
Con backend y frontend activos y el seeder ejecutado:
```bash
cd uis/backoffice
npx playwright install chromium
npm run test:e2e
```

Comprueba el proxy HTTP, CRUD y validaciones, conserva el endpoint de incidencias, y prueba filtros, formulario, errores API, tarifa, estado y capturas desktop/mobile. `TRACKFLOW_URL` cambia la URL objetivo. Los registros temporales se eliminan al terminar.

La prueba de navegador requiere las bibliotecas Linux de Chromium. Si falta `libatk-1.0.so.0`, ejecuta `npx playwright install-deps chromium` en una terminal con permisos de administrador y repite las pruebas. Sin ellas, puedes validar solo la integracion HTTP: `npm run test:e2e -- --grep 'API integrada'`.
