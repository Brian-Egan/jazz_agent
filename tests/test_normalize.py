import pytest

from jazz_agent.core.normalize import normalize_act_name

REQUIRED_CASES = [
    ("The Bill Frisell Four", "Bill Frisell"),
    ("Ravi Coltrane Quartet", "Ravi Coltrane"),
    ("Ari Hoenig Trio - 10:30 PM Set", "Ari Hoenig"),
]

ADDITIONAL_CASES = [
    ("Gerald Clayton Legacy Trio", "Gerald Clayton"),
    ("Sonny Rollins and Friends", "Sonny Rollins"),
    ("Sonny Rollins - Early Set", "Sonny Rollins"),
    ("Sonny Rollins - $20 cover", "Sonny Rollins"),
    ("The Cookers", "The Cookers"),  # a real band name, not "bandleader + ensemble"
]


@pytest.mark.parametrize(("raw", "expected"), REQUIRED_CASES + ADDITIONAL_CASES)
def test_normalize_act_name(raw: str, expected: str) -> None:
    assert normalize_act_name(raw) == expected


def test_tribute_act_is_not_reduced_to_the_honouree() -> None:
    raw = "A Love Supreme: The Music of John Coltrane"

    result = normalize_act_name(raw)

    assert result == raw
    assert result != "John Coltrane"


def test_normalize_act_name_never_mutates_its_input() -> None:
    raw = "The Bill Frisell Four"

    normalize_act_name(raw)

    assert raw == "The Bill Frisell Four"


# --- CapitalBop act names (DC section 2) ---

from jazz_agent.core.normalize import clean_act_name, normalize_listing_name  # noqa: E402

LISTING_CASES = [
    ("Lalah Hathaway ***SOLD OUT***", "Lalah Hathaway"),
    ("Kofi B ***SOLD OUT***", "Kofi B"),
    (
        "John Scofield: &#8220;Electrospective&#8221; **SOLD OUT**",
        "John Scofield: “Electrospective”",
    ),
    ("Melvis Santa&#8217;s Gata Jazz Trio", "Melvis Santa’s Gata Jazz"),
    (
        "Rhythmic Tales: William Hooker &#038; On Ka’a Davis",
        "Rhythmic Tales: William Hooker & On Ka’a Davis",
    ),
    ("CANCELLED: Some Act", "Some Act"),
    ("Some Act - POSTPONED", "Some Act"),
    ("Some Act (SOLD OUT)", "Some Act"),
    ("Some Act CANCELLED", "Some Act"),
    ("Some Act", "Some Act"),
]


@pytest.mark.parametrize(("raw", "expected"), LISTING_CASES)
def test_normalize_listing_name(raw: str, expected: str) -> None:
    assert normalize_listing_name(raw) == expected


def test_status_marker_and_plain_name_are_one_act() -> None:
    assert normalize_listing_name("Lalah Hathaway ***SOLD OUT***") == normalize_listing_name(
        "Lalah Hathaway"
    )


def test_clean_act_name_leaves_lowercase_words_inside_a_name_alone() -> None:
    # Only delimited or all-caps markers are stripped; band names are not mangled.
    assert clean_act_name("Sold Out Shows Collective") == "Sold Out Shows Collective"
    assert clean_act_name("The Postponed Quartet") == "The Postponed Quartet"
    assert clean_act_name("Band that got sold out") == "Band that got sold out"


def test_clean_act_name_does_not_strip_ensemble_suffixes() -> None:
    assert clean_act_name("Gerald Clayton Trio") == "Gerald Clayton Trio"
