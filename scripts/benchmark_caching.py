import logging
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from fastapi.testclient import TestClient

from main import create_app
from models import Incident, IncidentBranch, IncidentCategory, IncidentOrigin, IncidentStatus


class TimingCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.samples = []

    def emit(self, record):
        self.samples.append(record.args[3])


def measure(client: TestClient, path: str, timing: TimingCapture) -> str:
    samples = []
    timing.samples.clear()
    for _ in range(10):
        started = time.perf_counter()
        response = client.get(path)
        response.raise_for_status()
        samples.append((time.perf_counter() - started) * 1000)
    return f"HTTP median {statistics.median(samples):.2f} ms; middleware median {statistics.median(timing.samples):.2f} ms"


def main() -> None:
    timing = TimingCapture()
    logger = logging.getLogger("uvicorn.error.timing")
    logger.setLevel(logging.INFO)
    logger.addHandler(timing)
    logger.propagate = False
    with tempfile.TemporaryDirectory(prefix="trackflow-caching-") as temporary:
        directory = Path(temporary)
        os.environ["DATABASE_URL"] = f"sqlite:///{directory / 'inventory.sqlite'}"
        os.environ["SECRET_KEY"] = "temporary-benchmark-secret-at-least-32-characters"
        os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
        with TestClient(create_app(directory / "incidents.json")) as client:
            client.post("/users", json={"email": "benchmark@example.com", "password": "benchmark-password"}).raise_for_status()
            login = client.post("/auth/login", json={"email": "benchmark@example.com", "password": "benchmark-password"})
            login.raise_for_status()
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            paths = ["/api/incidents", "/api/incidents/summary"]
            for path in paths:
                print(f"0 incidents {path}: {measure(client, path, timing)}")

            categories = get_args(IncidentCategory)
            origins = get_args(IncidentOrigin)
            branches = get_args(IncidentBranch)
            statuses = list(IncidentStatus)
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            documents = []
            for index in range(3000):
                date = start + timedelta(hours=index * 3)
                incident = Incident(
                    id=uuid5(NAMESPACE_URL, f"trackflow-benchmark-{index}"),
                    title=f"{categories[index % len(categories)]} shipment {index}",
                    description=f"Seguimiento operativo del envio {index} en {branches[(index // 27) % len(branches)]}.",
                    category=categories[index % len(categories)],
                    origin=origins[(index // 9) % len(origins)],
                    branch=branches[(index // 27) % len(branches)],
                    status=statuses[(index // 135) % len(statuses)],
                    created_at=date,
                    updated_at=date,
                )
                documents.append(incident.model_dump(mode="json"))
            client.app.state.db.table("incidents").insert_multiple(documents)
            client.app.state.incident_cache.clear()
            for path in paths:
                print(f"3000 incidents {path}: {measure(client, path, timing)}")


if __name__ == "__main__":
    main()