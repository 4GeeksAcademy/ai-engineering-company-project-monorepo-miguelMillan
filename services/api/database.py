import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from fastapi import FastAPI, Request
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine
from tinydb import TinyDB

from models import SKU, StockEntry, StockExit, client_id_from_name
from security import auth_settings


def database_path() -> Path:
    return Path(os.environ.get("SUPPLIERS_DB_PATH", Path(__file__).parent / "data" / "suppliers.json"))


def open_database(path: Path | None = None) -> TinyDB:
    location = path if path is not None else database_path()
    location.parent.mkdir(parents=True, exist_ok=True)
    return TinyDB(location)


def create_sql_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Configura DATABASE_URL en services/api/.env.")
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg2")
    return create_engine(url, pool_pre_ping=True)


def initialize_sql_database(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("sku")}
    with engine.begin() as connection:
        if "client_id" not in columns:
            connection.execute(text("ALTER TABLE sku ADD COLUMN client_id VARCHAR(64)"))
        if "minimum_stock" not in columns:
            connection.execute(text("ALTER TABLE sku ADD COLUMN minimum_stock INTEGER"))
        legacy_skus = connection.execute(
            text("SELECT id, client_name FROM sku WHERE client_id IS NULL OR client_id = ''")
        ).mappings()
        for sku in legacy_skus:
            connection.execute(
                text("UPDATE sku SET client_id = :client_id WHERE id = :id"),
                {"id": sku["id"], "client_id": client_id_from_name(sku["client_name"])},
            )


def database_lifespan(path: Path | None = None):
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        auth_settings()
        application.state.db = open_database(path)
        application.state.lock = RLock()
        application.state.incident_cache = {}
        engine = None
        try:
            engine = create_sql_engine()
            application.state.engine = engine
            initialize_sql_database(engine)
            yield
        finally:
            if engine is not None:
                engine.dispose()
            application.state.db.close()

    return lifespan


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session