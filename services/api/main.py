import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import database_lifespan
from models import HealthResponse
from routes.actions import router as actions_router
from routes.auth import router as auth_router
from routes.incidents import router as incidents_router
from routes.knowledge import router as knowledge_router
from routes.agent import router as agent_router
from routes.inventory import catalog_router, router as inventory_router
from routes.profiles import router as profiles_router
from routes.suppliers import router
from routes.telemetry import router as telemetry_router
from routes.tasks import router as tasks_router
from routes.users import router as users_router
from routes.chat import router as chat_router
from routes.rfp_events import router as rfp_events_router
from routes.rfp_intake import router as rfp_intake_router

logger = logging.getLogger("uvicorn.error.timing")
# API JSON: nada se renderiza ni se enmarca. (HSTS lo pone el proxy que termina TLS.) Swagger, solo fuera de producción.
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}
DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


def create_app(path: Path | None = None) -> FastAPI:
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from services.reporting.routes import router as reporting_router

    production = os.environ.get("APP_ENV", "development") == "production"
    docs = {"openapi_url": None, "docs_url": None, "redoc_url": None} if production else {}
    application = FastAPI(title="TrackFlow Supplier Directory", lifespan=database_lifespan(path), **docs)

    @application.middleware("http")
    async def timing_middleware(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path not in DOCS_PATHS:
            response.headers.update(SECURITY_HEADERS)
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s -> %s | %.1fms", request.method, request.url.path, response.status_code, duration_ms)
        return response

    @application.get("/health", response_model=HealthResponse)
    async def health():
        return {"status": "ok", "service": "trackflow-suppliers"}

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path != "/api/incidents" and not request.url.path.startswith("/api/incidents/"):
            return await request_validation_exception_handler(request, error)
        messages = {
            "missing": "Este campo es obligatorio.",
            "string_type": "Debe ser un texto.",
            "string_too_short": "El texto no puede estar vacio.",
            "value_error": "El texto no puede estar vacio ni contener solo espacios.",
            "extra_forbidden": "Este campo no esta permitido.",
            "json_invalid": "El cuerpo debe contener JSON valido.",
            "uuid_parsing": "El identificador debe ser un UUID valido.",
            "uuid_type": "El identificador debe ser un UUID valido.",
            "model_attributes_type": "El cuerpo debe ser un objeto JSON con los campos de la incidencia.",
        }
        errors = []
        for item in error.errors():
            field = ".".join(str(part) for part in item["loc"][1:]) or "body"
            if item["type"] in {"enum", "literal_error"}:
                message = "Valor no permitido. Valores admitidos: " + item.get("ctx", {}).get("expected", "consulta el catalogo de incidencias")
            else:
                message = messages.get(item["type"], "El valor indicado no es valido para este campo.")
            errors.append({"field": field, "message": message})
        return JSONResponse(status_code=400, content={"detail": "Revisa los campos de la incidencia.", "errors": errors})

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        return JSONResponse(status_code=500, content={"detail": "Error interno del servidor. Intentalo de nuevo mas tarde."},
                            headers=SECURITY_HEADERS)

    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5174,http://127.0.0.1:5174")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins.split(","),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "authorization", "last-event-id", "x-confirmation"],
    )
    application.include_router(router)
    application.include_router(users_router)
    application.include_router(profiles_router)
    application.include_router(auth_router)
    application.include_router(incidents_router)
    application.include_router(inventory_router)
    application.include_router(catalog_router)
    application.include_router(telemetry_router)
    application.include_router(tasks_router)
    application.include_router(knowledge_router)
    application.include_router(agent_router)
    application.include_router(rfp_intake_router)
    application.include_router(rfp_events_router)
    application.include_router(chat_router)
    application.include_router(actions_router)
    application.include_router(reporting_router)
    return application


app = create_app()


def main():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("SUPPLIERS_PORT", "8081")),
                timeout_graceful_shutdown=5)  # un stream SSE abierto no debe bloquear el apagado