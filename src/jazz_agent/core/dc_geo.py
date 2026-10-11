"""DC city resolution (DC section 3). Pure."""

from __future__ import annotations

from collections.abc import Iterable

# First hit wins in resolve_city. Neighborhood slugs are CapitalBop's own taxonomy.
NEIGHBORHOOD_CITIES = {
    "dc": "Washington",
    "alexandria": "Alexandria",
    "arlington": "Arlington",
}

WANTED_CITIES = frozenset(NEIGHBORHOOD_CITIES.values())


def resolve_city(
    city_override: str | None,
    geocoded_city: str | None,
    neighborhood_slugs: Iterable[str],
) -> str | None:
    """Resolve a show's city: hand-set override, then the event's geocoded city, then
    neighborhood slugs. ``None`` means unknown, which fails the geography filter and must be
    logged by the caller so a missing override is visible rather than silent."""
    for candidate in (city_override, geocoded_city):
        if candidate and candidate.strip():
            return candidate.strip()
    slugs = set(neighborhood_slugs)
    for slug, city in NEIGHBORHOOD_CITIES.items():
        if slug in slugs:
            return city
    return None
