import dataclasses
from datetime import date

import pytest

from jazz_agent.core.models import (
    AlbumChoice,
    ArtistMatch,
    ExtractedShow,
    Performer,
    Show,
    WeekPlaylist,
)


def test_show_act_name_raw_and_norm_are_independent_and_frozen() -> None:
    show = Show(
        club_id="village-vanguard",
        show_date=date(2026, 5, 5),
        act_name_raw="The Bill Frisell Four",
        act_name_norm="Bill Frisell",
    )

    assert show.act_name_raw == "The Bill Frisell Four"
    assert show.act_name_norm == "Bill Frisell"

    with pytest.raises(dataclasses.FrozenInstanceError):
        show.act_name_raw = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "instance",
    [
        Performer(name="Gerald Clayton", instrument="piano"),
        ExtractedShow(show_date=date(2026, 5, 5), act_name="Gerald Clayton Trio"),
        ArtistMatch(spotify_artist_id="abc123", confidence=0.9, reasoning="exact name match"),
        AlbumChoice(spotify_album_id="alb1", spotify_artist_id="abc123", track_ids=("t1", "t2")),
        WeekPlaylist(
            club_id="village-vanguard",
            week_start_date=date(2026, 5, 5),
            title="This Week at Village Vanguard - May 5-11",
            description="",
        ),
    ],
)
def test_models_are_frozen(instance: object) -> None:
    field = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, getattr(instance, field))
