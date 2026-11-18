from datetime import date

from services.job_runner import create_job, has_completed_for_date, has_processing_lock


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, results):
        self.results = iter(results)
        self.executed = []

    def execute(self, statement, parameters=None):
        self.executed.append((str(statement), parameters))
        result = next(self.results)
        return _ScalarResult(result)


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, results):
        self.connection = _Connection(results)

    def begin(self):
        return _Transaction(self.connection)

    def connect(self):
        return _Transaction(self.connection)


def test_processing_check_is_limited_to_nightly_export():
    engine = _Engine([True])

    assert has_processing_lock(engine)
    sql, params = engine.connection.executed[0]
    assert "job_name = :job_name AND status = 'processing'" in sql
    assert params == {"job_name": "nightly_export"}


def test_completed_check_uses_job_name_and_target_date():
    engine = _Engine([True])

    assert has_completed_for_date(engine, date(2026, 11, 17))
    sql, params = engine.connection.executed[0]
    assert "target_date = :target_date" in sql
    assert params == {"job_name": "nightly_export", "target_date": date(2026, 11, 17)}


def test_create_job_records_duplicate_without_processing_transition():
    engine = _Engine([None, False, True, "duplicate-run"])

    run_id, reason = create_job(engine, date(2026, 11, 17))

    assert run_id == "duplicate-run"
    assert reason == "completed"
    sql = "\n".join(statement for statement, _ in engine.connection.executed)
    assert "LOCK TABLE public.job_runs" in sql
    assert "'completed'" in sql
    assert "UPDATE public.job_runs" not in sql


def test_create_job_records_cancellation_when_processing_row_exists():
    engine = _Engine([None, True, "cancelled-run"])

    run_id, reason = create_job(engine, date(2026, 11, 17))

    assert run_id == "cancelled-run"
    assert reason == "processing"
    sql = "\n".join(statement for statement, _ in engine.connection.executed)
    assert "Cancelled: another nightly_export run is processing." in sql
    assert "INSERT INTO public.job_runs" in sql
