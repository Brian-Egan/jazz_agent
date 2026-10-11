import pytest

from jazz_agent.core.dc_geo import resolve_city


def test_resolution_order_is_override_then_geocoded_then_neighborhood() -> None:
    assert resolve_city("Alexandria", "Washington", ["arlington"]) == "Alexandria"
    assert resolve_city(None, "Bethesda", ["dc", "arlington"]) == "Bethesda"
    assert resolve_city(None, None, ["northwest", "dc"]) == "Washington"
    assert resolve_city(None, None, ["virginia", "arlington"]) == "Arlington"
    assert resolve_city(None, None, ["alexandria"]) == "Alexandria"


@pytest.mark.parametrize("slugs", [[], ["virginia", "northwest"]])
def test_unknown_when_nothing_resolves(slugs: list[str]) -> None:
    assert resolve_city(None, None, slugs) is None


def test_blank_override_and_geocode_fall_through() -> None:
    assert resolve_city("  ", "", ["dc"]) == "Washington"
