from jazz_agent.core.selection import order_tracks, select_album


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
