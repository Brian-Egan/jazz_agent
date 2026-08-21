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
