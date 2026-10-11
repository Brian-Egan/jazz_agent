"""Act-name normalization: recover a bandleader name from a club listing's headline text.

The caller (Show construction) is responsible for keeping the original as
act_name_raw; this module never mutates its input, only returns a cleaned copy.
"""

from __future__ import annotations

import html
import re

# Longer, more specific phrases first so "Legacy Trio" strips as a unit rather
# than leaving a dangling "Legacy" after a naive "Trio" match.
_ENSEMBLE_SUFFIXES = (
    "legacy trio",
    "and friends",
    "quartet",
    "quintet",
    "trio",
    "four",
)

_TRAILING_SET_TIME = re.compile(
    r"""[\s\-–—:]+
        (?:
            (?:early|late|first|second)\s+set
            |\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*(?:set)?
            |set
        )
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TRAILING_PRICE = re.compile(
    r"""[\s\-–—:(]+
        (?:\$\s*\d+(?:\.\d{2})?|\d+(?:\.\d{2})?\s*(?:cover|door|adv))
        [^\-–—]*
        $
    """,
    re.IGNORECASE | re.VERBOSE,
)

_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)


def normalize_act_name(raw: str) -> str:
    """Strip listing chrome and a known ensemble suffix to recover a bandleader name.

    Order: trailing set-time/label text, trailing ticket/price text, then a
    single trailing ensemble suffix. A leading "The" is stripped only when an
    ensemble suffix was actually removed -- "The Bill Frisell Four" is a
    bandleader-plus-ensemble pattern, but "The Cookers" is a band's actual
    name and must survive untouched. Anything that matches none of these
    fixed patterns -- including tribute-act titles -- passes through
    unchanged: there is no heuristic here that infers a person's name from
    prose, so a tribute act is never misidentified as the artist it honours.
    """
    name = raw.strip()

    previous = None
    while previous != name:
        previous = name
        name = _TRAILING_SET_TIME.sub("", name).strip()
        name = _TRAILING_PRICE.sub("", name).strip()

    stripped_ensemble_suffix = False
    lowered = name.lower()
    for suffix in _ENSEMBLE_SUFFIXES:
        if lowered.endswith(suffix):
            name = name[: -len(suffix)].strip()
            stripped_ensemble_suffix = True
            break

    if stripped_ensemble_suffix:
        name = _LEADING_THE.sub("", name).strip()

    return name


# --- CapitalBop act names (DC section 2) -------------------------------------------------

_STATUS_WORDS = r"(?:sold\s*out|cancel+ed|postponed)"

# `***SOLD OUT***`, `**SOLD OUT**`, `(CANCELLED)`: delimited markers are unambiguous in any case.
_DELIMITED_STATUS = re.compile(
    rf"\*+\s*{_STATUS_WORDS}\s*\*+|[(\[]\s*{_STATUS_WORDS}\s*[)\]]", re.IGNORECASE
)
# `CANCELLED: Act`, `Act - POSTPONED`: needs punctuation to separate it from the name.
_EDGE_STATUS = re.compile(
    rf"^\s*{_STATUS_WORDS}\s*[:\-–—]\s*|\s*[:\-–—]\s*{_STATUS_WORDS}\s*$", re.IGNORECASE
)
# Bare `Act CANCELLED` is accepted only in capitals, so a band whose name happens to end in
# "sold out" is not mangled.
_BARE_UPPER_STATUS = re.compile(r"\s+(?:SOLD OUT|CANCELL?ED|POSTPONED)\s*$")
_SPACES = re.compile(r"\s+")


def clean_act_name(raw: str) -> str:
    """Decode HTML entities and strip status markers from a CapitalBop act name.

    ``Lalah Hathaway ***SOLD OUT***`` and ``Lalah Hathaway`` must be one act, so this runs
    before deduplication and matching (DC section 2). It does not strip ensemble suffixes;
    use ``normalize_listing_name`` for the full chain.
    """
    name = html.unescape(raw)
    previous = None
    while previous != name:
        previous = name
        name = _DELIMITED_STATUS.sub(" ", name)
        name = _EDGE_STATUS.sub("", name)
        name = _BARE_UPPER_STATUS.sub("", name)
        name = _SPACES.sub(" ", name).strip()
    return name


def normalize_listing_name(raw: str) -> str:
    """The identity of a CapitalBop act: entity decoding and status markers first, then the
    existing suffix stripping (DC section 2)."""
    return normalize_act_name(clean_act_name(raw))
