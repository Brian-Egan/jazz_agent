from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.feedback_repo import PgFeedbackRepo
from jazz_agent.core.models import Feedback


def test_record_and_fetch_feedback_round_trips(db: ConnectionPool) -> None:
    repo = PgFeedbackRepo(db)

    feedback_id = repo.record_feedback(
        Feedback(
            target_type="artist",
            target_id="artist1",
            sentiment="liked",
            note_text="loved the interplay with the drummer",
        )
    )

    fetched = repo.feedback_for_target("artist", "artist1")

    assert feedback_id > 0
    assert fetched == [
        Feedback(
            target_type="artist",
            target_id="artist1",
            sentiment="liked",
            note_text="loved the interplay with the drummer",
        )
    ]


def test_feedback_for_target_is_scoped_by_type_and_id(db: ConnectionPool) -> None:
    repo = PgFeedbackRepo(db)
    repo.record_feedback(Feedback(target_type="artist", target_id="artist1", sentiment="liked"))
    repo.record_feedback(Feedback(target_type="track", target_id="artist1", sentiment="disliked"))
    repo.record_feedback(Feedback(target_type="artist", target_id="artist2", sentiment="liked"))

    fetched = repo.feedback_for_target("artist", "artist1")

    assert len(fetched) == 1
    assert fetched[0].sentiment == "liked"
