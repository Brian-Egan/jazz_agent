import uuid

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.run_repo import PgRunRepo
from jazz_agent.core.models import RunLogEntry


def _insert_club(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            ("village-vanguard", "Village Vanguard", "https://villagevanguard.com"),
        )


def test_log_and_fetch_run_outcome_round_trips(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgRunRepo(db)
    run_id = str(uuid.uuid4())

    repo.log_run_outcome(
        RunLogEntry(
            run_id=run_id,
            outcome="success",
            club_id="village-vanguard",
            shows_found=3,
            detail=None,
            duration_ms=842,
        )
    )

    recent = repo.recent_runs(club_id="village-vanguard")

    assert recent == [
        RunLogEntry(
            run_id=run_id,
            outcome="success",
            club_id="village-vanguard",
            shows_found=3,
            detail=None,
            duration_ms=842,
        )
    ]


def test_recent_runs_without_club_id_returns_all_clubs(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgRunRepo(db)
    repo.log_run_outcome(
        RunLogEntry(run_id=str(uuid.uuid4()), outcome="success", club_id="village-vanguard")
    )
    repo.log_run_outcome(RunLogEntry(run_id=str(uuid.uuid4()), outcome="fetch_fail", club_id=None))

    recent = repo.recent_runs()

    assert len(recent) == 2
