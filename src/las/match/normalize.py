"""One explicit, limited name-normalization algorithm.

Scope, and why it is this small
-------------------------------
The synthetic contract generates account and source names from templates:
``Demo Account 000007``, ``Synthetic Twin Account 900001``,
``Unlisted Source 3``. They contain letters, digits and single spaces, and
nothing else. So normalization does the least that makes two spellings of the
same name comparable, and no more.

Applied, in order:

1. Unicode NFKC normalization, folding compatibility forms so a full-width
   character cannot masquerade as a different name from its ASCII twin.
2. Whitespace collapse: every run of Unicode whitespace becomes one space, and
   leading and trailing whitespace is dropped.
3. Case folding, Unicode-aware.

Deliberately absent: transliteration, phonetic keys, language rules, synonym
or abbreviation tables, fuzzy correction, and punctuation stripping. The
fixtures contain no punctuation, so removing any would be inventing a rule the
contract never states — punctuation is compared literally.

Length is a safety cap, not matching logic
------------------------------------------
Nothing in the development plan, the configuration models, the sample
configuration or the fixture generator states a maximum name length — the
longest name the contract produces is twenty-nine characters. The cap exists
only to bound the scoring work, so it is a *parameter* rather than a hidden
constant: callers pass it explicitly, and
:data:`las.match.policy.MatchPolicy.max_name_length` owns the value that
public matching actually uses. :data:`DEFAULT_MAX_NAME_LENGTH` is only the
default that policy starts from.

Unusable names
--------------
:func:`normalize_name` returns ``None`` rather than a string when a name
cannot be compared safely: absent, blank, longer than the supplied
``max_length``, or containing a *non-whitespace* control or format
character such as a NUL, an escape, a zero-width space, or a bidirectional
override. A row with an unusable name is ineligible for name-based matching;
it is never repaired into a candidate.

Whitespace control characters — tab, newline, carriage return and friends — are
whitespace, not hostile content, so step 2 collapses them like any other space
rather than refusing the name.

The caller's original string is never modified.
"""

from __future__ import annotations

import unicodedata

#: Default safety cap on a comparable name. Not a business rule: the contract
#: states no limit, and this only bounds the scoring work.
DEFAULT_MAX_NAME_LENGTH = 256

#: Hard ceiling on how large a caller may set that cap, so a policy cannot ask
#: for unbounded comparison work.
MAX_CONFIGURABLE_NAME_LENGTH = 4096

#: Unicode categories that make a name uncomparable when the character is not
#: whitespace: control characters, format characters (zero-width joiners,
#: bidirectional overrides), surrogates, private use, and unassigned points.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def name_length_fault(value: object) -> str | None:
    """Why a proposed cap is unusable, or ``None`` when it is fine.

    Returned as text rather than raised so that the policy and the master
    index can each raise their own typed error from one shared rule. The bound
    means the same thing on both sides, so it is described in exactly one
    place and cannot drift.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return "max_name_length must be an integer"
    if not 1 <= value <= MAX_CONFIGURABLE_NAME_LENGTH:
        return f"max_name_length must lie between 1 and {MAX_CONFIGURABLE_NAME_LENGTH}"
    return None


def is_comparable(
    text: str | None, *, max_length: int = DEFAULT_MAX_NAME_LENGTH
) -> bool:
    """Whether a raw name can be normalized into a comparison key."""
    return normalize_name(text, max_length=max_length) is not None


def normalize_name(
    text: str | None, *, max_length: int = DEFAULT_MAX_NAME_LENGTH
) -> str | None:
    """Return the comparison key for a name, or ``None`` when unusable.

    Idempotent: normalizing an already-normalized key returns it unchanged.
    ``max_length`` is the safety cap described in the module docstring; the
    engine and the index always pass it explicitly from the policy.
    """
    if not isinstance(text, str):
        return None
    if len(text) > max_length:
        return None
    if any(
        not character.isspace()
        and unicodedata.category(character) in _REFUSED_CATEGORIES
        for character in text
    ):
        return None

    folded = " ".join(unicodedata.normalize("NFKC", text).split()).casefold()
    return folded or None
