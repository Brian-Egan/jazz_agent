"""core/removals.py: inferring a user's removal from a complete read (DC section 11)."""

from __future__ import annotations

from jazz_agent.core.models import RollingPlaylistTrack
from jazz_agent.core.removals import detect_removals


def stint(n: int, isrc: str | None = "auto") -> RollingPlaylistTrack:
    return RollingPlaylistTrack(
        id=n,
        rolling_playlist_id=1,
        spotify_track_id=f"t{n}",
        spotify_album_id="alb",
        spotify_artist_id="artist",
        position=n,
        isrc=f"ISRC{n}" if isrc == "auto" else isrc,
    )


def live(*ids: str, isrcs: dict[str, str] | None = None) -> list[dict[str, str | None]]:
    return [{"id": i, "isrc": (isrcs or {}).get(i, f"ISRC{i[1:]}")} for i in ids]


def test_a_stint_absent_from_the_read_is_a_confirmed_removal() -> None:
    verdict = detect_removals([stint(n) for n in range(1, 5)], live("t1", "t2", "t4"))

    assert [s.spotify_track_id for s in verdict.confirmed] == ["t3"]
    assert not verdict.anomaly


def test_nothing_missing_confirms_nothing() -> None:
    verdict = detect_removals([stint(1), stint(2)], live("t1", "t2"))

    assert verdict.confirmed == () and not verdict.anomaly and verdict.missing_count == 0


def test_a_relinked_track_is_not_a_removal() -> None:
    # t3 was re-served under a new id; same recording, same ISRC.
    verdict = detect_removals(
        [stint(n) for n in range(1, 5)],
        live("t1", "t2", "new-id", "t4", isrcs={"new-id": "ISRC3"}),
    )

    assert verdict.confirmed == () and verdict.missing_count == 0


def test_a_stint_with_no_recorded_isrc_is_judged_by_track_id_alone() -> None:
    verdict = detect_removals([stint(n, isrc=None) for n in range(1, 5)], live("t1", "t2", "t3"))

    assert [s.spotify_track_id for s in verdict.confirmed] == ["t4"]


def test_more_than_half_missing_is_an_anomaly_and_confirms_nothing() -> None:
    verdict = detect_removals([stint(n) for n in range(1, 6)], live("t1", "t2"))

    assert verdict.anomaly and verdict.confirmed == ()
    assert (verdict.missing_count, verdict.live_stint_count) == (3, 5)


def test_exactly_half_missing_is_not_an_anomaly() -> None:
    verdict = detect_removals([stint(n) for n in range(1, 5)], live("t1", "t2"))

    assert not verdict.anomaly
    assert [s.spotify_track_id for s in verdict.confirmed] == ["t3", "t4"]


def test_a_playlist_with_no_open_stints_has_nothing_to_detect() -> None:
    verdict = detect_removals([], live("t1"))

    assert verdict == detect_removals([], [])
    assert verdict.confirmed == () and not verdict.anomaly
