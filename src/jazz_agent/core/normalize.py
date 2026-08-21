"""Act-name normalization: recover a bandleader name from a club listing's headline text.

The caller (Show construction) is responsible for keeping the original as
act_name_raw; this module never mutates its input, only returns a cleaned copy.
"""

from __future__ import annotations

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
