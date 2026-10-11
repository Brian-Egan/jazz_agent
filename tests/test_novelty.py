"""core/novelty.py (DC section 9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jazz_agent.core.novelty import FeedbackEntry, excluded_artist_ids, excluded_track_ids

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def fb(target: str, sentiment: str | None, days_ago: float, kind: str = "artist") -> FeedbackEntry:
    return FeedbackEntry(kind, target, sentiment, NOW - timedelta(days=days_ago))


def test_disliked_excludes_permanently() -> None:
    assert excluded_artist_ids([fb("a", "disliked", 3000)], NOW) == {"a"}


def test_a_later_liked_lifts_an_earlier_disliked_then_expires() -> None:
    history = [fb("a", "disliked", 30), fb("a", "liked", 10)]

    assert excluded_artist_ids(history, NOW) == {"a"}  # liked, so out for six months
    assert excluded_artist_ids(history, NOW + timedelta(days=173)) == set()  # liked now >182d old


def test_a_later_disliked_beats_an_earlier_liked() -> None:
    assert excluded_artist_ids([fb("a", "liked", 400), fb("a", "disliked", 1)], NOW) == {"a"}


def test_liked_expires_at_182_days() -> None:
    assert excluded_artist_ids([fb("a", "liked", 181)], NOW) == {"a"}
    assert excluded_artist_ids([fb("a", "liked", 182)], NOW) == set()
    assert excluded_artist_ids([fb("a", "liked", 100)], NOW, liked_days=90) == set()


def test_neutral_and_note_only_have_no_effect() -> None:
    assert excluded_artist_ids([fb("a", "neutral", 1), fb("b", None, 1)], NOW) == set()


def test_the_latest_row_decides_even_when_it_is_a_note() -> None:
    # DC section 9: latest artist-level feedback decides; a note-only row has no effect, so it
    # leaves nothing excluded rather than inheriting the earlier dislike.
    assert excluded_artist_ids([fb("a", "disliked", 5), fb("a", None, 1)], NOW) == set()


def test_input_order_does_not_matter() -> None:
    rows = [fb("a", "liked", 10), fb("a", "disliked", 30)]

    assert excluded_artist_ids(rows, NOW) == excluded_artist_ids(rows[::-1], NOW) == {"a"}


def test_track_dislike_excludes_only_the_track() -> None:
    rows = [fb("t1", "disliked", 5, kind="track"), fb("t2", "liked", 5, kind="track")]

    assert excluded_track_ids(rows) == {"t1"}
    assert excluded_artist_ids(rows, NOW) == set()  # never excludes an artist by itself


def test_a_later_track_like_lifts_a_track_dislike() -> None:
    rows = [fb("t1", "disliked", 5, kind="track"), fb("t1", "liked", 1, kind="track")]

    assert excluded_track_ids(rows) == set()


def test_album_feedback_is_ignored() -> None:
    assert excluded_artist_ids([fb("x", "disliked", 1, kind="album")], NOW) == set()
