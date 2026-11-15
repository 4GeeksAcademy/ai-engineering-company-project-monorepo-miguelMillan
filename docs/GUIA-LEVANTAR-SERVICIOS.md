# Guía para levantar los servicios de TrackFlow

## Opción recomendada: Docker Compose

Desde la raíz del repositorio:

```bash
cd /workspaces/ai-engineering-company-project-monorepo-miguelMillan
```

### 1. Preparar las variables de entorno

Obtén el archivo `.env` por el canal seguro del equipo. No lo compartas ni
ejecutes `docker compose config`, porque la salida puede contener secretos.

El archivo debe incluir las variables requeridas por
[`docker-compose.yml`](../docker-compose.yml), entre ellas:

- `BIND_ADDRESS`
- `WEBSITE_PORT`
- `BACKOFFICE_PORT`
- `API_PORT`
- `AUTH_PORT`
- `DATABASE_URL`
- `SECRET_KEY`
- `DOCKER_NETWORK_NAME`
- Variables de proxy y health checks

### 2. Primera ejecución

Construye las imágenes y levanta todos los servicios:

```bash
docker compose up -d --build --wait
```

El stack incluye:

- Website público
- Backoffice
- Gateway Node en el puerto `8080`
- API FastAPI en el puerto `8081`

Para ver los logs en primer plano:

```bash
docker compose up --build
```

### 3. Comprobar el estado

```bash
docker compose ps
curl http://localhost:8080/health
curl http://localhost:8081/health
```

Direcciones locales:

```text
http://localhost:3000       # Website
http://localhost:3001       # Backoffice
http://localhost:8081/docs  # Documentación FastAPI
```

En Codespaces, abre los puertos publicados desde la pestaña **Ports** de VS
Code.

### 4. Consultar logs

Todos los servicios:

```bash
docker compose logs -f
```

Solo el backend:

```bash
docker compose logs -f backend
```

Solo las interfaces:

```bash
docker compose logs -f interfaces
```

### 5. Arranques posteriores

Cuando las imágenes ya estén construidas:

```bash
docker compose up -d --wait
```

### 6. Detener los servicios

Detén los contenedores conservando los datos:

```bash
docker compose down
```

No uses `down -v` salvo que quieras eliminar los volúmenes y sus datos:

```bash
docker compose down -v
```

## Opción manual: sin Docker

Esta alternativa requiere Node.js, Python 3.10 o superior y
[uv](https://docs.astral.sh/uv/getting-started/installation/).

### Terminal 1: backend Node y FastAPI

```bash
cd /workspaces/ai-engineering-company-project-monorepo-miguelMillan/services/api
uv sync
```

Configura `services/api/.env` con una `SECRET_KEY` y la `DATABASE_URL`
correspondientes. Para desarrollo puedes generar una clave local:

```bash
umask 077
printf 'SECRET_KEY=%s\nACCESS_TOKEN_EXPIRE_MINUTES=30\n' "$(openssl rand -hex 32)" > .env
```

Si necesitas cargar los proveedores iniciales, ejecuta el seed con el backend
detenido:

```bash
uv run seed
```

Después levanta ambos servicios:

```bash
npm run dev
```

Esto inicia:

- Gateway Node en `http://localhost:8080`
- FastAPI en `http://localhost:8081`

### Terminal 2: website

```bash
cd /workspaces/ai-engineering-company-project-monorepo-miguelMillan/uis/website
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Abre `http://localhost:5173`.

### Terminal 3: backoffice

```bash
cd /workspaces/ai-engineering-company-project-monorepo-miguelMillan/uis/backoffice
npm install
npm run dev -- --host 0.0.0.0 --port 5174
```

Abre `http://localhost:5174`.

El backoffice utiliza el gateway Node en `http://localhost:8080` y FastAPI en
`http://localhost:8081`.

## Secuencia rápida

```bash
cd /workspaces/ai-engineering-company-project-monorepo-miguelMillan
docker compose up -d --build --wait
docker compose ps
curl http://localhost:8080/health
curl http://localhost:8081/health
docker compose down
```

