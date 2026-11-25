"""Exposición de red y del gateway (docs/security/owasp-audit.md, CR-5 y CR-7). Contra el código anterior, fallan."""

import http.client
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICES = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
PUBLIC = {"interfaces", "backend"}   # lo que sirve Caddy; su dirección la fija BIND_ADDRESS (127.0.0.1 en el servidor)


@pytest.mark.parametrize("name", sorted(set(SERVICES) - PUBLIC))
def test_cr7_internal_services_are_never_published_beyond_loopback(name):
    assert all(str(port).startswith("127.0.0.1:") for port in SERVICES[name].get("ports", []))


@pytest.mark.parametrize("name", ["backend", "mcp", "celery-worker", "flower"])
def test_cr7_containers_run_without_root_nor_capabilities(name):
    service = SERVICES[name]
    assert service.get("user", "root").startswith("${APP_UID") and service.get("cap_drop") == ["ALL"]
    assert service.get("security_opt") == ["no-new-privileges:true"]


@pytest.mark.parametrize("name", ["celery-worker", "flower", "mcp"])
def test_cr7_only_the_backend_receives_every_secret(name):
    service = SERVICES[name]
    assert "env_file" not in service
    assert not {"SECRET_KEY", "AGENT_OAUTH_CLIENT_SECRET", "RESEND_API_KEY"} & set(service["environment"])


@pytest.fixture
def gateway():
    """El gateway Node real, con un API que no existe: si la petición llega a reenviarse, responde 503."""
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = free_port()
    process = subprocess.Popen(["node", "services/api/src/server.js"], cwd=ROOT, stdout=subprocess.DEVNULL, env={
        **os.environ, "PORT": str(port), "SUPPLIERS_PORT": str(free_port()), "TASKS_INTERNAL_TOKEN": "secreto-del-gateway",
        "CORS_ORIGINS": "https://app.trackflow.example"})
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)

    def request(method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response.read()
        return response

    yield request
    process.terminate()
    process.wait(5)


def test_cr5_the_gateway_does_not_forward_uploads_without_a_session(gateway):
    body = b'--b\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n\r\na\n1\n\r\n--b--\r\n'
    headers = {"Content-Type": "multipart/form-data; boundary=b"}
    assert gateway("POST", "/api/incidents/analyze", headers, body).status == 401
    assert gateway("POST", "/api/incidents/analyze", {**headers, "Authorization": "Bearer "}, body).status == 401
    assert gateway("POST", "/api/incidents/analyze", {**headers, "Authorization": "Bearer x"}, body).status == 503  # lo decide el API


@pytest.mark.parametrize("origin,granted", [("https://evil.example", None), ("https://app.trackflow.example", "https://app.trackflow.example")])
def test_cr5_the_gateway_only_grants_cors_to_the_trackflow_origins(gateway, origin, granted):
    for method, path in (("GET", "/health"), ("OPTIONS", "/suppliers"), ("GET", "/suppliers"), ("GET", "/no-existe")):
        assert gateway(method, path, {"Origin": origin}).getheader("access-control-allow-origin") == granted


def test_cr5_the_internal_adapter_is_not_reachable_through_the_gateway(gateway):
    for path in ("/internal/tasks/analyze", "//internal/tasks/analyze"):
        assert gateway("POST", path, {"x-internal-task-token": "adivinado", "Authorization": "Bearer x"}).status == 404


# ---------------------------------------------------------------- el informe no puede quedarse desactualizado
REPORT = (ROOT / "docs/security/owasp-audit.md").read_text(encoding="utf-8") if (ROOT / "docs/security/owasp-audit.md").exists() else ""


def test_every_category_of_every_lane_cites_code_that_exists():
    lanes = [part for part in re.split(r"^## ", REPORT, flags=re.M) if re.match(r"[345]\. Carril", part)]
    assert len(lanes) == 3
    for lane in lanes:
        rows = re.findall(r"^\| \*\*(A\d\d)[^|]*\|(.*)$", lane, re.M)
        assert [code for code, _ in rows] == [f"A{n:02d}" for n in range(1, 11)]
        for code, row in rows:
            cited = re.findall(r"`([\w./-]+?\.(?:py|js|ts|tsx|yml|txt|json|example|lock|sh))(?::(\d+))?`", row)
            assert any(line for _, line in cited), f"{lane.splitlines()[0]} {code}: sin archivo:línea"
            for path, line in cited:
                file = ROOT / path
                assert file.is_file(), f"{code}: no existe {path}"
                assert not line or int(line) <= len(file.read_text(encoding="utf-8").splitlines()), f"{code}: {path}:{line}"
