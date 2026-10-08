# TrackFlow API Service

Servicio backend para operaciones internas de TrackFlow.

## Estructura de entrega
```text
services/api/
	main.py              # aplicacion FastAPI y arranque
	models.py            # modelos Pydantic de entrada y respuesta
	database.py          # inicializacion y cierre de TinyDB
	routes/suppliers.py  # endpoints del directorio
	seed.py              # proveedores exactos del contexto
```

`src/server.js` conserva la API Node de incidencias y el proxy del directorio; `src/start.js` arranca ambos servicios. El directorio tambien funciona directamente como aplicacion FastAPI con `uv run uvicorn main:app --host 0.0.0.0 --port 8081`.

## Endpoints
- `GET /health` -> devuelve estado del servicio.
- `POST /suppliers` -> crea un proveedor (201) con `id` de TinyDB y `updated_at` UTC generados por el sistema.
- `GET /suppliers` -> lista completa; filtros opcionales `country=USA|Spain` y `category=<categoria>`, combinables.
- `GET /suppliers/{id}` -> detalle, o 404.
- `PATCH /suppliers/{id}/rate` -> cuerpo `{"rate_per_shipment": 8.25}`; actualiza automaticamente `updated_at`.
- `PATCH /suppliers/{id}/status` -> cuerpo `{"status": "active"}` o `{"status": "suspended"}`.
- `DELETE /suppliers/{id}` -> elimina (204), o 404. En operaciones se recomienda suspender en lugar de eliminar.
- `POST /api/incidents/analyze` -> recibe `multipart/form-data` con un archivo `.csv` en el campo `file`; responde con totales, desglose por categoria, estado y pais, CSAT e incidencias de validacion.
- `GET /api/incidents/results/export` -> descarga como `results.csv` el ultimo analisis completado en esta instancia.

Los archivos se limitan a 10 MB. Los CSV con esquema invalido o no procesables devuelven un error descriptivo. El servidor reutiliza el procesador Python ubicado en `scripts/incidents_analysis.py`; requiere Python 3.10 o posterior y puede configurarse con `PYTHON`.

## Ejecutar en local
Requisitos: Node.js y Python >= 3.10 con [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
cd services/api
uv sync
uv run seed
npm run dev
```

`uv run seed` inserta los 15 proveedores exactos de `CONTEXT.md`, imprime el numero de inserciones y no duplica registros existentes con el mismo nombre y pais ni sobrescribe sus cambios. Ejecutarlo con el servicio detenido.

`npm run dev` inicia la API Node en `0.0.0.0:8080` y FastAPI en `0.0.0.0:8081`. Node conserva incidencias y redirige `/suppliers` a FastAPI. Para ejecutar solo el directorio: `uv run serve`. Documentacion interactiva: `http://localhost:8081/docs`.

La persistencia por defecto es `services/api/data/suppliers.json` (excluida de Git). `SUPPLIERS_DB_PATH` cambia la ubicacion tanto para API como para seeder; `SUPPLIERS_PORT` cambia el puerto Python y su proxy Node; `PORT` cambia el puerto Node. `CORS_ORIGINS` admite una lista separada por comas de origenes autorizados para acceso directo a FastAPI.

Pydantic rechaza entradas invalidas con 422 antes de persistir: tarifa no positiva o no finita, nombre vacio, categorias vacias/desconocidas, estados distintos de `active`/`suspended`, y combinaciones distintas de USA/USD o Spain/EUR. Campos opcionales: `service_zone`, `contact_email` (email valido), `notes`. El cliente no puede enviar `id` ni `updated_at`.

TinyDB usa un bloqueo para lecturas/escrituras dentro del proceso. Ejecutar **una sola instancia/worker** y no ejecutar el seeder mientras la API esta activa; TinyDB no es adecuado para escrituras multiproceso. `updated_at` registra la ultima tarifa, no un historial completo de tarifas o suspensiones.

## Pruebas
```bash
cd services/api
uv run pytest -q
```

29 pruebas con bases temporales: validacion, CRUD, filtros por pais/categoria y combinados, timestamps, errores 404/422, idempotencia del seeder, comparacion exacta con `CONTEXT.md` y persistencia despues de reiniciar un proceso Uvicorn real.
