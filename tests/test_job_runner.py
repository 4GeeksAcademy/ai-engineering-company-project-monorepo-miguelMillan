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


# ----------------------------------------------------------------------------------------------
# create_job: el lock es el índice ÚNICO parcial de `processing` (sin LOCK TABLE)
# Se prueba contra SQLite con el mismo índice: la violación de unicidad es real, no simulada.
# ----------------------------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from services import job_runner  # noqa: E402

MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261121100000_job_runs_unique_processing_lock.sql"


@pytest.fixture
def db():
    # Una sola conexión (StaticPool) para compartir la base en memoria; `public` como esquema adjunto.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _rec: conn.execute("ATTACH DATABASE ':memory:' AS public"))
    with engine.begin() as connection:
        connection.execute(text(
            """CREATE TABLE public.job_runs (
                   id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                   job_name TEXT NOT NULL, target_date DATE NOT NULL,
                   status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
                   started_at TIMESTAMP, finished_at TIMESTAMP, error_message TEXT)"""))
        # Mismo índice que la migración (SQLite: el nombre del índice lleva el esquema)
        connection.execute(text(
            "CREATE UNIQUE INDEX public.ux_job_runs_one_processing_per_job ON job_runs (job_name) WHERE status = 'processing'"))
    return engine


def rows(engine):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT id, target_date, status, started_at IS NOT NULL, finished_at IS NOT NULL, error_message "
            "FROM public.job_runs ORDER BY rowid")).all()


def test_create_job_takes_the_lock_with_an_atomic_pending_to_processing_transition(db):
    run_id, reason = job_runner.create_job(db, date(2026, 11, 12))
    assert reason is None
    assert rows(db) == [(run_id, "2026-11-12", "processing", 1, 0, None)]


def test_second_instance_is_cancelled_by_the_unique_processing_index_not_by_a_table_lock(db):
    first, _ = job_runner.create_job(db, date(2026, 11, 12))
    second, reason = job_runner.create_job(db, date(2026, 11, 12))
    assert reason == "processing"
    assert rows(db) == [
        (first, "2026-11-12", "processing", 1, 0, None),
        (second, "2026-11-12", "failed", 0, 1, job_runner.CANCELLED_MESSAGE),
    ]


def test_the_index_alone_rejects_a_second_processing_row(db):
    job_runner.create_job(db, date(2026, 11, 12))
    with pytest.raises(Exception, match="UNIQUE"):
        with db.begin() as connection:
            connection.execute(text(
                "INSERT INTO public.job_runs (job_name, target_date, status) VALUES ('nightly_export', '2026-11-11', 'processing')"))


def test_lock_is_released_when_the_run_finishes_and_the_day_is_then_a_duplicate(db):
    first, _ = job_runner.create_job(db, date(2026, 11, 12))
    job_runner.update_job(db, first, status="completed")
    duplicate, reason = job_runner.create_job(db, date(2026, 11, 12))
    assert reason == "completed"
    assert rows(db)[-1] == (duplicate, "2026-11-12", "completed", 0, 1, None)
    other_day, reason = job_runner.create_job(db, date(2026, 11, 11))  # otro día: el lock está libre
    assert reason is None


def test_create_job_never_locks_the_table():
    import inspect

    assert "LOCK TABLE" not in inspect.getsource(job_runner.create_job).replace("No hay LOCK TABLE", "")


def test_completed_meanwhile_rolls_back_the_claim_and_records_a_duplicate():
    """Otra ejecución completó el día entre el UPDATE y la re-comprobación: no se procesa dos veces."""
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class Conn:
        def __init__(self, engine):
            self.engine = engine

        def execute(self, statement, params=None):
            self.engine.executed.append(str(statement))
            return Result(self.engine.results.pop(0))

    class Tx:
        def __init__(self, engine):
            self.engine = engine

        def __enter__(self):
            return Conn(self.engine)

        def __exit__(self, exc_type, *_):
            self.engine.outcomes.append("rollback" if exc_type else "commit")
            return False

    class Engine:
        def __init__(self, results):
            self.results, self.executed, self.outcomes = results, [], []

        def begin(self):
            return Tx(self)

    engine = Engine(["run-1", "run-1", True, None])  # insert pending, claim, completed_meanwhile, finish
    run_id, reason = job_runner.create_job(engine, date(2026, 11, 12))
    assert (run_id, reason) == ("run-1", "completed")
    assert engine.outcomes == ["commit", "rollback", "commit"]  # el claim se deshizo
    assert "status = 'pending'" in engine.executed[-1]


def test_migration_replaces_the_non_unique_index_with_a_unique_partial_one():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "DROP INDEX IF EXISTS public.ix_job_runs_processing" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_job_runs_one_processing_per_job" in sql
    assert "ON public.job_runs (job_name)" in sql and "WHERE status = 'processing'" in sql
