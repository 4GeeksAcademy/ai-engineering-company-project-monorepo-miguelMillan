from uuid import uuid4


def test_submit_incident_csv_returns_accepted_task(authenticated_client, monkeypatch, tmp_path):
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
    response = authenticated_client.post(
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


def test_submit_incident_csv_validates_extension(authenticated_client):
    response = authenticated_client.post(
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


def test_task_status_returns_result(authenticated_client, monkeypatch):
    report = {"totals": {"total_records": 1}}

    class SuccessfulTask:
        state = "SUCCESS"
        result = {"report": report}

        def successful(self):
            return True

        def failed(self):
            return False

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: SuccessfulTask())
    response = authenticated_client.get(f"/tasks/{uuid4()}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["result"] == report


def test_task_status_reports_lifecycle_states(authenticated_client, monkeypatch):
    for celery_state in ("PENDING", "STARTED", "RETRY", "FAILURE", "REVOKED"):
        class TaskState:
            state = celery_state
            result = RuntimeError("private error")

            def successful(self):
                return celery_state == "SUCCESS"

            def failed(self):
                return celery_state in {"FAILURE", "REVOKED"}

        monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: TaskState())
        response = authenticated_client.get(f"/tasks/{uuid4()}")
        assert response.status_code == 200
        expected_state = "failure" if celery_state in {"FAILURE", "REVOKED"} else celery_state.lower()
        assert response.json()["status"] == expected_state
        if expected_state == "failure":
            assert response.json()["error"] == "La tarea agotó sus reintentos y no pudo completarse."


def test_analysis_task_uses_three_exponential_retries():
    from tasks import IncidentAnalysisTask

    assert IncidentAnalysisTask.max_retries == 3
    assert IncidentAnalysisTask.retry_backoff is True
    assert IncidentAnalysisTask.retry_jitter is True
    assert IncidentAnalysisTask.autoretry_for == (Exception,)


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


def test_export_requires_successful_task(authenticated_client, monkeypatch):
    class PendingTask:
        def successful(self):
            return False

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: PendingTask())
    response = authenticated_client.get(f"/api/incidents/results/export?task_id={uuid4()}")
    assert response.status_code == 409


def test_export_returns_csv_for_successful_task(authenticated_client, monkeypatch):
    class SuccessfulTask:
        result = {"results_csv": "metric,value\ntotal_records,1\n"}

        def successful(self):
            return True

    monkeypatch.setattr("routes.tasks.AsyncResult", lambda *args, **kwargs: SuccessfulTask())
    response = authenticated_client.get(f"/api/incidents/results/export?task_id={uuid4()}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "total_records,1" in response.text