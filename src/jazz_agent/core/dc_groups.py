"""The three DC groups and three rolling playlists (DC section 4, ADR-018).

Hardcoded on purpose: a group means a Spotify playlist, and the MCP server must never be
able to cause one to exist (ADR-021). Adding a fourth group is a code change here, a
matching change to the CHECK constraints in the next migration, and a deploy.
migrations/0003_dc.sql mirrors GROUP_KEYS, PROFILES and REMOVAL_REASONS;
tests/test_dc_schema.py fails if the two drift.
"""

from __future__ import annotations

from dataclasses import dataclass

REGION = "dc"

FILTER_PROFILES = ("all", "standard", "standard_no_pick", "early_evening")
DEFAULT_PROFILE = "standard"

# DC section 10. 'user' is invariant 9's reason and the only one detection ever writes.
REMOVAL_REASONS = ("rolled_off", "moved", "ineligible", "correction", "user")


@dataclass(frozen=True, slots=True)
class PlaylistDef:
    """One rolling playlist. ``(region, slot_key)`` is invariant 4's key for DC."""

    slot_key: str
    group_key: str
    title: str
    is_public: bool
    # Maximum artists on the playlist, None = uncapped (DC section 10).
    cap: int | None = None
    region: str = REGION


@dataclass(frozen=True, slots=True)
class GroupDef:
    key: str
    # A dedicated group takes a venue exclusively: a venue belongs to at most one, and is
    # then excluded from the dc CB Pick rule so no show lands in two playlists (DC section 4).
    dedicated: bool
    playlists: tuple[PlaylistDef, ...]


DC_CAP = 12  # artists on the dc playlist

GROUPS: tuple[GroupDef, ...] = (
    GroupDef(
        key="dc",
        dedicated=False,
        playlists=(PlaylistDef("next-6-weeks", "dc", "DC Jazz · Next 6 Weeks", True, DC_CAP),),
    ),
    GroupDef(
        key="blues-alley",
        dedicated=True,
        playlists=(PlaylistDef("blues-alley", "blues-alley", "Blues Alley · Next 6 Weeks", True),),
    ),
    GroupDef(
        key="alxjazz",
        dedicated=True,
        playlists=(PlaylistDef("alxjazz", "alxjazz", "AlxJazz · Next 6 Weeks", False),),
    ),
)

GROUP_KEYS = tuple(g.key for g in GROUPS)
DEDICATED_GROUP_KEYS = tuple(g.key for g in GROUPS if g.dedicated)
PLAYLISTS: tuple[PlaylistDef, ...] = tuple(p for g in GROUPS for p in g.playlists)


def group(key: str) -> GroupDef:
    for g in GROUPS:
        if g.key == key:
            return g
    raise KeyError(f"unknown DC group {key!r}; valid groups: {', '.join(GROUP_KEYS)}")


def is_dedicated(group_key: str) -> bool:
    return group(group_key).dedicated
