import pytest

from jazz_agent.core.selection import (
    normalize_track_title,
    order_tracks,
    select_album,
    select_tracks,
)


def _album(
    name: str, popularity: int, release_date: str = "2020-01-01", album_id: str = ""
) -> dict:
    return {
        "id": album_id or name,
        "name": name,
        "popularity": popularity,
        "release_date": release_date,
    }


def test_clear_winner_by_popularity() -> None:
    albums = [
        _album("Small Melody", 12),
        _album("Big Sky", 78),
        _album("Quiet Morning", 45),
    ]

    result = select_album(albums)

    assert result.reason is None
    assert result.album is not None
    assert result.album["name"] == "Big Sky"


def test_all_zero_popularity_falls_back_to_most_recent_release() -> None:
    albums = [
        _album("Early Session", 0, release_date="1998-03-01"),
        _album("Later Session", 0, release_date="2015-11-05"),
        _album("Middle Session", 0, release_date="2007-06-20"),
    ]

    result = select_album(albums)

    assert result.reason is None
    assert result.album is not None
    assert result.album["name"] == "Later Session"


def test_below_popularity_floor_falls_back_to_recency_not_just_zero() -> None:
    albums = [
        _album("Older but slightly popular", 4, release_date="1998-01-01"),
        _album("Newer, also below floor", 3, release_date="2020-01-01"),
    ]

    result = select_album(albums)

    assert result.album is not None
    assert result.album["name"] == "Newer, also below floor"


def test_greatest_hits_is_rejected() -> None:
    albums = [
        _album("Greatest Hits", 90),  # highest popularity, but a compilation
        _album("Original Sessions", 20),
    ]

    result = select_album(albums)

    assert result.reason is None
    assert result.album is not None
    assert result.album["name"] == "Original Sessions"


def test_various_compilation_titles_are_rejected() -> None:
    for title in ["The Best Of Miles Davis", "Anthology", "Jazz Collection", "The Very Best"]:
        albums = [_album(title, 99), _album("A Real Studio Album", 10)]

        result = select_album(albums)

        assert result.album is not None
        assert result.album["name"] == "A Real Studio Album", f"failed to reject {title!r}"


def test_live_album_is_eligible_not_rejected() -> None:
    """ARCHITECTURE.md section 7 only lists compilations, greatest-hits, and
    obvious reissues as rejected by title -- a live album is a legitimate
    album and should be selectable like any other."""
    albums = [
        _album("Live at the Village Vanguard", 55),
        _album("Studio Sessions", 20),
    ]

    result = select_album(albums)

    assert result.album is not None
    assert result.album["name"] == "Live at the Village Vanguard"


def test_obvious_reissue_is_rejected() -> None:
    albums = [
        _album("Kind of Blue (Legacy Edition)", 95),
        _album("Kind of Blue", 40),
    ]

    result = select_album(albums)

    assert result.album is not None
    assert result.album["name"] == "Kind of Blue"


def test_remastered_alone_is_not_treated_as_a_reissue() -> None:
    """Nearly all catalog jazz on streaming is remastered; rejecting that tag
    alone would leave most artists with nothing eligible."""
    albums = [_album("A Love Supreme (Remastered)", 80)]

    result = select_album(albums)

    assert result.album is not None
    assert result.album["name"] == "A Love Supreme (Remastered)"


def test_no_eligible_album_yields_no_album_and_a_reason_not_a_crash() -> None:
    albums = [_album("Greatest Hits", 99), _album("Anthology", 50)]

    result = select_album(albums)

    assert result.album is None
    assert result.reason == "NO_ELIGIBLE_ALBUM"


def test_no_albums_at_all_yields_no_album_and_a_reason() -> None:
    result = select_album([])

    assert result.album is None
    assert result.reason == "NO_ELIGIBLE_ALBUM"


def test_order_tracks_matches_track_number() -> None:
    tracks = [
        {"id": "t3", "track_number": 3},
        {"id": "t1", "track_number": 1},
        {"id": "t2", "track_number": 2},
    ]

    ordered = order_tracks(tracks)

    assert [t["id"] for t in ordered] == ["t1", "t2", "t3"]


def test_order_tracks_across_multi_disc_albums() -> None:
    tracks = [
        {"id": "d2t1", "disc_number": 2, "track_number": 1},
        {"id": "d1t2", "disc_number": 1, "track_number": 2},
        {"id": "d1t1", "disc_number": 1, "track_number": 1},
        {"id": "d2t2", "disc_number": 2, "track_number": 2},
    ]

    ordered = order_tracks(tracks)

    assert [t["id"] for t in ordered] == ["d1t1", "d1t2", "d2t1", "d2t2"]


def test_order_tracks_defaults_missing_disc_number_to_one() -> None:
    tracks = [{"id": "t2", "track_number": 2}, {"id": "t1", "track_number": 1}]

    ordered = order_tracks(tracks)

    assert [t["id"] for t in ordered] == ["t1", "t2"]


# --- select_tracks (DC section 8) ---------------------------------------------------------

ME = "artist-me"


def _t(track_id: str, name: str, album: str, artist: str = ME, **extra: object) -> dict:
    return {
        "id": track_id,
        "name": name,
        "album": {"id": album},
        "artists": [{"id": artist}, {"id": "someone-else"}],
        **extra,
    }


def _ids(tracks: list[dict]) -> list[str]:
    return [t["id"] for t in tracks]


def test_sideman_appearances_are_excluded() -> None:
    results = [
        _t("side", "Blue in Green", "a-other", artist="leader"),  # first artist is not ours
        _t("mine1", "So What", "a1"),
        _t("mine2", "Freddie", "a2"),
    ]

    assert _ids(select_tracks(ME, results, [])) == ["mine1", "mine2"]


def test_a_track_where_we_are_only_second_listed_does_not_count() -> None:
    track = {"id": "t", "name": "X", "album": {"id": "a"}, "artists": [{"id": "boss"}, {"id": ME}]}

    assert select_tracks(ME, [track], []) == []


def test_live_remaster_and_alternate_take_duplicates_are_dropped() -> None:
    results = [
        _t("orig", "So What", "a1"),
        _t("live", "So What - Live at the Vanguard", "a2"),
        _t("remaster", "So What (Remastered 2011)", "a3"),
        _t("alt", "So What - Alternate Take", "a4"),
        _t("other", "Blue in Green", "a5"),
    ]

    assert _ids(select_tracks(ME, results, [])) == ["orig", "other"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("So What - Live", "so what"),
        ("So What - Live at Montreux, 1991", "so what"),
        ("So What (Live)", "so what"),
        ("So What (Remastered 2009)", "so what"),
        ("So What - 2011 Remaster", "so what"),
        ("So What - Remastered", "so what"),
        ("So What Alternate Take", "so what"),
        ("Alive Again", "alive again"),  # "live" inside a word is not a version marker
        ("Blue (Part 2)", "blue (part 2)"),  # an unrelated bracket stays
    ],
)
def test_title_normalisation(title: str, expected: str) -> None:
    assert normalize_track_title(title) == expected


def test_two_different_albums_are_preferred_over_two_from_one_album() -> None:
    results = [
        _t("a1-first", "One", "alb1"),
        _t("a1-second", "Two", "alb1"),
        _t("a2-first", "Three", "alb2"),
    ]

    assert _ids(select_tracks(ME, results, [])) == ["a1-first", "a2-first"]


def test_same_album_is_accepted_when_there_is_no_other() -> None:
    results = [_t("x", "One", "alb1"), _t("y", "Two", "alb1")]

    assert _ids(select_tracks(ME, results, [])) == ["x", "y"]


def test_fallback_fills_from_the_opening_tracks_of_the_album() -> None:
    album_tracks = [
        {"id": "trk3", "name": "Third", "track_number": 3},
        {"id": "trk1", "name": "First", "track_number": 1},
        {"id": "trk2", "name": "Second", "track_number": 2},
    ]
    results = [_t("found", "Only One", "alb1")]

    assert _ids(select_tracks(ME, results, album_tracks)) == ["found", "trk1"]
    assert _ids(select_tracks(ME, [], album_tracks)) == ["trk1", "trk2"]


def test_fallback_skips_a_title_already_chosen() -> None:
    album_tracks = [
        {"id": "alb-sowhat", "name": "So What (Remastered)", "track_number": 1},
        {"id": "alb-2", "name": "Freddie", "track_number": 2},
    ]
    results = [_t("found", "So What", "alb1")]

    assert _ids(select_tracks(ME, results, album_tracks)) == ["found", "alb-2"]


def test_fallback_album_is_fetched_only_when_needed() -> None:
    calls: list[int] = []

    def fetch() -> list[dict]:
        calls.append(1)
        return [{"id": "f1", "name": "F", "track_number": 1}]

    full = [_t("a", "One", "x"), _t("b", "Two", "y")]
    select_tracks(ME, full, fetch)
    assert calls == []

    select_tracks(ME, [], fetch)
    assert calls == [1]


def test_selection_order_is_search_rank_order() -> None:
    results = [_t("r1", "One", "alb1"), _t("r2", "Two", "alb1"), _t("r3", "Three", "alb2")]

    assert _ids(select_tracks(ME, results, [])) == ["r1", "r3"]
