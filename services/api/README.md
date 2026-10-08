# TrackFlow API Service

Servicio backend para operaciones internas de TrackFlow.

## Endpoints
- `GET /health` -> devuelve estado del servicio.
- `POST /api/incidents/analyze` -> recibe `multipart/form-data` con un archivo `.csv` en el campo `file`; responde con totales, desglose por categoria, estado y pais, CSAT e incidencias de validacion.
- `GET /api/incidents/results/export` -> descarga como `results.csv` el ultimo analisis completado en esta instancia.

Los archivos se limitan a 10 MB. Los CSV con esquema invalido o no procesables devuelven un error descriptivo. El servidor reutiliza el procesador Python ubicado en `scripts/incidents_analysis.py`; requiere Python 3.10 o posterior y puede configurarse con `PYTHON`.

## Ejecutar en local
```bash
cd services/api
npm run dev
```

## Nota
El servidor escucha en `0.0.0.0:8080` por defecto para compatibilidad con Codespaces.
