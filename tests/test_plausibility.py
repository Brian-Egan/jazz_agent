from jazz_agent.core.plausibility import PLAUSIBILITY_FLOOR, plausibility_score


def _candidate(genres: list[str], followers: int = 1000, popularity: int = 20) -> dict:
    return {"genres": genres, "followers": {"total": followers}, "popularity": popularity}


def test_working_jazz_act_scores_above_floor() -> None:
    bill_frisell = _candidate(
        ["contemporary jazz", "jazz fusion", "jazz"], followers=123_456, popularity=42
    )

    assert plausibility_score(bill_frisell) > PLAUSIBILITY_FLOOR


def test_same_named_non_jazz_act_is_rejected() -> None:
    """A same-named act from an unrelated genre, with a large following --
    exactly the ADR-007 failure mode this guard exists to catch."""
    same_named_metal_band = _candidate(
        ["death metal", "thrash metal"], followers=2_000_000, popularity=65
    )

    assert plausibility_score(same_named_metal_band) < PLAUSIBILITY_FLOOR


def test_small_working_musician_with_no_genre_tags_is_not_disqualified() -> None:
    """Low followers/popularity alone must never sink a candidate -- most working
    jazz musicians are small, and that's normal, not a signal of being wrong."""
    obscure_no_genres = _candidate([], followers=12, popularity=0)

    assert plausibility_score(obscure_no_genres) >= PLAUSIBILITY_FLOOR


def test_unrelated_genre_with_small_following_is_still_low_but_not_extra_penalized() -> None:
    small_wrong_genre = _candidate(["k-pop"], followers=50, popularity=1)

    assert plausibility_score(small_wrong_genre) < PLAUSIBILITY_FLOOR


def test_score_is_bounded_between_zero_and_one() -> None:
    assert 0.0 <= plausibility_score(_candidate(["jazz"], followers=10**9)) <= 1.0
    assert 0.0 <= plausibility_score(_candidate(["polka"], followers=10**9)) <= 1.0
    assert 0.0 <= plausibility_score(_candidate([], followers=0)) <= 1.0


def test_missing_fields_do_not_raise() -> None:
    assert 0.0 <= plausibility_score({}) <= 1.0
    assert 0.0 <= plausibility_score({"genres": ["jazz"]}) <= 1.0
