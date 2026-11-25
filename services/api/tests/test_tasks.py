from uuid import uuid4


def test_submit_incident_csv_returns_accepted_task(staff_client, monkeypatch, tmp_path):
    task_id = str(uuid4())
    monkeypatch.setenv("TASK_INPUT_DIR", str(tmp_path))

    class FakeTask:
        id = task_id

    def fake_apply_async(*, args, task_id):
        assert args == [task_id]
        assert (tmp_path / f"{task_id}.csv").read_text() == "incident_id,country\n1,US\n"
        assert task_id
        return FakeTask()

    monkeypatch.setattr("routes.tasks.analyze_incident_csv.apply_async", fake_apply_async)
    response = staff_client.post(
        "/api/incidents/analyze",
        headers={"x-task-id": task_id},
        files={"file": ("incidents.csv", "incident_id,country\n1,US\n", "text/csv")},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": task_id, "status_url": f"/tasks/{task_id}"}


def test_task_input_is_loaded_only_by_reference(monkeypatch, tmp_path):
    from task_storage import delete_task_input, load_task_input, store_task_input

    task_id = str(uuid4())
    monkeypatch.setenv("TASK_INPUT_DIR", str(tmp_path))
    content = b"incident_id,country\n1,US\n"

    store_task_input(task_id, content)
    assert load_task_input(task_id) == content.decode()
    delete_task_input(task_id)
    assert not (tmp_path / f"{task_id}.csv").exists()


def test_submit_incident_csv_validates_extension(staff_client):
    response = staff_client.post(
        "/api/incidents/analyze",
        headers={"x-task-id": str(uuid4())},
        files={"file": ("incidents.txt", "not a csv", "text/plain")},
    )
    assert response.status_code == 400


def test_gateway_enqueue_adapter_requires_internal_token(api_client, monkeypatch):
    monkeypatch.setenv("TASKS_INTERNAL_TOKEN", "expected-secret")
    response = api_client.post(
        "/internal/tasks/analyze",
        files={"file": ("incidents.csv", "incident_id,country\n1,US\n", "text/csv")},
    )
    assert response.status_code == 401


def test_task_status_returns_result(staff_client, monkeypatch):
    report = {"totals": {"total_records": 1}}

    class SuccessfulTask:
        state = "SUCCESS"
        result = {"report": report}

        def successful(self):
            return True

        def failed(self):
            return False

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: SuccessfulTask())
    response = staff_client.get(f"/tasks/{uuid4()}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["result"] == report


def test_task_status_reports_lifecycle_states(staff_client, monkeypatch):
    for celery_state in ("PENDING", "STARTED", "RETRY", "FAILURE", "REVOKED"):
        class TaskState:
            state = celery_state
            result = RuntimeError("private error")

            def successful(self):
                return celery_state == "SUCCESS"

            def failed(self):
                return celery_state in {"FAILURE", "REVOKED"}

        monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: TaskState())
        response = staff_client.get(f"/tasks/{uuid4()}")
        assert response.status_code == 200
        expected_state = "failure" if celery_state in {"FAILURE", "REVOKED"} else celery_state.lower()
        assert response.json()["status"] == expected_state
        if expected_state == "failure":
            assert response.json()["error"] == "La tarea agotó sus reintentos y no pudo completarse."


def test_analysis_task_uses_three_retries_without_celery_autoretry_jitter():
    from tasks import IncidentAnalysisTask

    assert IncidentAnalysisTask.max_retries == 3
    # Celery autoretry + retry_jitter usa randrange(countdown + 1) y puede dar 0 s.
    assert not getattr(IncidentAnalysisTask, "autoretry_for", ())


def test_retry_countdown_has_floor_and_bounded_jitter():
    from tasks import RETRY_MAX_SECONDS, retry_countdown

    # Peor caso del jitter (rand=0): nunca baja del exponencial base 2, 4, 8 s.
    assert [retry_countdown(n, rand=lambda: 0.0) for n in range(3)] == [2, 4, 8]
    # Mejor caso: como mucho un 25 % adicional.
    assert [retry_countdown(n, rand=lambda: 1.0) for n in range(3)] == [2.5, 5.0, 10.0]
    assert retry_countdown(20, rand=lambda: 1.0) == RETRY_MAX_SECONDS
    assert min(retry_countdown(0) for _ in range(1000)) >= 2


def test_failure_schedules_retry_with_floor_countdown(monkeypatch):
    import pytest
    from celery.exceptions import Retry

    import tasks

    calls = {}

    def failing(_task_id):
        raise ValueError("CSV inválido")

    def fake_retry(*, exc, countdown):
        calls.update(exc=exc, countdown=countdown)
        return Retry(exc=exc, when=countdown)

    monkeypatch.setattr(tasks, "_analyze_stored_csv", failing)
    monkeypatch.setattr(tasks.analyze_incident_csv, "retry", fake_retry)
    tasks.analyze_incident_csv.push_request(retries=0)
    try:
        with pytest.raises(Retry):
            tasks.analyze_incident_csv.run(str(uuid4()))
    finally:
        tasks.analyze_incident_csv.pop_request()
    assert 2.0 <= calls["countdown"] <= 2.5
    assert isinstance(calls["exc"], ValueError)


def test_exhausted_retries_reraise_original_error(monkeypatch):
    import pytest

    import tasks

    def failing(_task_id):
        raise ValueError("CSV inválido")

    monkeypatch.setattr(tasks, "_analyze_stored_csv", failing)
    tasks.analyze_incident_csv.push_request(retries=3)
    try:
        with pytest.raises(ValueError, match="CSV inválido"):
            tasks.analyze_incident_csv.run(str(uuid4()))
    finally:
        tasks.analyze_incident_csv.pop_request()


def test_terminal_failure_is_recorded_in_dead_letter_table(monkeypatch):
    from types import SimpleNamespace

    from tasks import IncidentAnalysisTask

    captured = {}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters):
            captured["sql"] = str(statement)
            captured["parameters"] = parameters

        def commit(self):
            captured["committed"] = True

    class FakeEngine:
        def dispose(self):
            captured["disposed"] = True

    monkeypatch.setattr("tasks.create_sql_engine", FakeEngine)
    monkeypatch.setattr("tasks.Session", lambda engine: FakeSession())
    monkeypatch.setattr("tasks.delete_task_input", lambda task_id: captured.update(deleted=task_id))

    monkeypatch.setattr(IncidentAnalysisTask, "request", SimpleNamespace(retries=3))
    task = IncidentAnalysisTask()
    task.on_failure(RuntimeError("failed after retries"), str(uuid4()), (), {}, None)

    assert "public.task_dead_letters" in captured["sql"]
    assert captured["parameters"]["attempts"] == 4
    assert captured["parameters"]["error"] == "failed after retries"
    assert captured["committed"] is True
    assert captured["disposed"] is True


def test_export_requires_successful_task(staff_client, monkeypatch):
    class PendingTask:
        def successful(self):
            return False

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: PendingTask())
    response = staff_client.get(f"/api/incidents/results/export?task_id={uuid4()}")
    assert response.status_code == 409


def test_export_returns_csv_for_successful_task(staff_client, monkeypatch):
    class SuccessfulTask:
        result = {"results_csv": "metric,value\ntotal_records,1\n"}

        def successful(self):
            return True

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: SuccessfulTask())
    response = staff_client.get(f"/api/incidents/results/export?task_id={uuid4()}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "total_records,1" in response.text

def test_task_input_skips_fsync_by_default_and_honours_opt_in(monkeypatch, tmp_path):
    import task_storage

    calls = []
    monkeypatch.setenv("TASK_INPUT_DIR", str(tmp_path))
    monkeypatch.setattr(task_storage.os, "fsync", lambda fd: calls.append(fd))

    monkeypatch.delenv("TASK_INPUT_FSYNC", raising=False)
    first = str(uuid4())
    task_storage.store_task_input(first, b"a,b\n")
    assert calls == []
    assert (tmp_path / f"{first}.csv").read_bytes() == b"a,b\n"
    assert not list(tmp_path.glob("*.tmp"))

    monkeypatch.setenv("TASK_INPUT_FSYNC", "1")
    task_storage.store_task_input(str(uuid4()), b"a,b\n")
    assert len(calls) == 1
