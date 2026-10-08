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

## Build
```bash
npm run build
```
