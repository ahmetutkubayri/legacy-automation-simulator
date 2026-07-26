"""Deterministic header normalization and explicit alias resolution.

Nothing here guesses. There is no fuzzy matching, no edit distance and no
scoring: a source header either normalizes to a canonical field's own name
or it appears verbatim in an explicitly declared alias registry. Anything
else is an unmapped column, handled by the schema's stated policy.

Normalization algorithm
-----------------------
Applied identically to canonical field names, alias keys and source
headers, so all three meet in one comparable space:

1. Unicode NFKC normalization, folding compatibility forms (so a full-width
   ``Ｒ`` and an ASCII ``R`` compare equal).
2. Every separator character — ``_``, ``-``, ``.``, ``/``, ``\\`` — and
   every Unicode whitespace character becomes a single space.
3. Runs of spaces collapse to one, and leading/trailing spaces are dropped.
4. The result is case-folded (Unicode-aware, so it handles more than ASCII).

Worked examples: ``"Record Id"``, ``"record_id"``, ``"  RECORD   id "`` and
``"record-id"`` all normalize to ``"record id"``, which is exactly the
normalization of the canonical name ``record_id`` — so a canonical header
always resolves to itself. ``"REC_ID"`` normalizes to ``"rec id"``, which is
*not* a canonical name, so it resolves only through an explicit alias.

Because resolution is a dictionary lookup over normalized keys, the result
never depends on the order aliases were declared or on set iteration order.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from las.config import CANONICAL_FIELDS, FeedConfig
from las.ingest.errors import HeaderResolutionError, SchemaError

#: Characters treated as word separators and folded to a single space.
SEPARATOR_CHARACTERS = "_-./\\"

#: Canonical master-data fields, taken from the committed fixture contract
#: (``accounts.csv`` and ``account_links.csv``). All invented for this project.
ACCOUNT_FIELD_NAMES = (
    "account_ref",
    "account_name",
    "contact_email",
    "street",
    "city",
    "postal_code",
    "status",
)
ACCOUNT_LINK_FIELD_NAMES = ("external_ref", "account_ref")


def normalize_header(raw: str) -> str:
    """Fold one header into the comparable space described in the module docs."""
    text = unicodedata.normalize("NFKC", raw)
    folded = "".join(
        " " if (character in SEPARATOR_CHARACTERS or character.isspace()) else character
        for character in text
    )
    return " ".join(folded.split()).casefold()


class ExtraColumnPolicy(StrEnum):
    """What to do with a source column that maps to no canonical field.

    Never silent: every policy records the column in
    ``IngestResult.unmapped_headers``.
    """

    #: Keep the value, in ``IngestedRow.extras``, keyed by normalized header.
    RETAIN = "retain"
    #: Report the column but drop its values.
    IGNORE = "ignore"
    #: Refuse the file outright.
    REJECT = "reject"


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field a schema expects."""

    name: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SchemaError("a canonical field name must be non-blank")


@dataclass(frozen=True)
class IngestSchema:
    """An immutable set of canonical fields plus an explicit alias registry.

    Contradictions are rejected here, when the schema is *defined*, rather
    than being discovered later against some particular file.
    """

    fields: tuple[FieldSpec, ...]
    aliases: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    extra_columns: ExtraColumnPolicy = ExtraColumnPolicy.RETAIN
    name: str = "schema"

    def __post_init__(self) -> None:
        # Coerce first: a caller may legitimately pass any sequence, but the
        # schema must not go on holding something they can still append to.
        object.__setattr__(self, "fields", tuple(self.fields))
        if not self.fields:
            raise SchemaError("a schema must declare at least one canonical field")

        names = [spec.name for spec in self.fields]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise SchemaError(f"duplicate canonical field(s): {', '.join(duplicates)}")

        lookup: dict[str, str] = {}
        for spec in self.fields:
            key = normalize_header(spec.name)
            if not key:
                raise SchemaError(f"canonical field '{spec.name}' normalizes to nothing")
            if key in lookup:
                raise SchemaError(
                    f"canonical fields '{lookup[key]}' and '{spec.name}' are "
                    "indistinguishable after normalization"
                )
            lookup[key] = spec.name

        declared = set(names)
        for raw_alias, target in self.aliases.items():
            if not raw_alias or not raw_alias.strip():
                raise SchemaError("an alias key must be non-blank")
            if target not in declared:
                raise SchemaError(
                    f"alias targets '{target}', which the schema does not declare"
                )
            key = normalize_header(raw_alias)
            if not key:
                raise SchemaError(f"alias '{raw_alias}' normalizes to nothing")
            existing = lookup.get(key)
            if existing is not None and existing != target:
                # Either two aliases collide, or an alias would shadow a
                # canonical name and quietly redirect it elsewhere.
                raise SchemaError(
                    f"alias for '{target}' collides with '{existing}' after "
                    "normalization"
                )
            lookup[key] = target

        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))
        object.__setattr__(self, "_lookup", MappingProxyType(lookup))

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields)

    @property
    def required_field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.required)

    @property
    def optional_field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if not spec.required)

    @property
    def lookup(self) -> Mapping[str, str]:
        """The immutable normalized-key to canonical-field mapping."""
        return self._lookup  # type: ignore[attr-defined]

    def resolve(self, normalized_header: str) -> str | None:
        """The canonical field for an already-normalized header, if any."""
        return self.lookup.get(normalized_header)


@dataclass(frozen=True)
class HeaderResolution:
    """The outcome of mapping one source header row onto a schema."""

    #: Per source column: the canonical field, or ``None`` for an extra column.
    columns: tuple[str | None, ...]
    #: Per source column: the normalized source header.
    normalized: tuple[str, ...]
    #: Canonical fields in the order their columns appear.
    headers: tuple[str, ...]
    #: Normalized names of unmapped columns, in column order.
    unmapped: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("columns", "normalized", "headers", "unmapped"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def width(self) -> int:
        """How many columns the header row declared."""
        return len(self.columns)


def resolve_headers(
    schema: IngestSchema,
    raw_headers: Sequence[str | None],
    *,
    operation: str,
    source: str,
    worksheet: str | None = None,
) -> HeaderResolution:
    """Map a source header row onto ``schema``, or raise a typed error.

    Rejects blank headers, headers that duplicate one another after
    normalization, two columns claiming the same canonical field, and any
    missing required field. Column positions in messages are one-based.
    """
    if not raw_headers:
        raise HeaderResolutionError(
            "the header row has no columns",
            operation=operation,
            source=source,
            worksheet=worksheet,
        )

    columns: list[str | None] = []
    normalized: list[str] = []
    unmapped: list[str] = []
    seen_at: dict[str, int] = {}
    claimed_at: dict[str, int] = {}

    for position, raw in enumerate(raw_headers, start=1):
        key = normalize_header(raw) if raw is not None else ""
        if not key:
            raise HeaderResolutionError(
                "a header cell is blank",
                operation=operation,
                source=source,
                worksheet=worksheet,
                column=position,
            )
        if key in seen_at:
            raise HeaderResolutionError(
                f"header '{key}' repeats the header at column {seen_at[key]}",
                operation=operation,
                source=source,
                worksheet=worksheet,
                column=position,
            )
        seen_at[key] = position
        normalized.append(key)

        canonical = schema.resolve(key)
        if canonical is None:
            if schema.extra_columns is ExtraColumnPolicy.REJECT:
                raise HeaderResolutionError(
                    f"header '{key}' maps to no canonical field and this schema "
                    "rejects extra columns",
                    operation=operation,
                    source=source,
                    worksheet=worksheet,
                    column=position,
                )
            columns.append(None)
            unmapped.append(key)
            continue

        if canonical in claimed_at:
            # An alias must never let two source columns become one field.
            raise HeaderResolutionError(
                f"canonical field '{canonical}' is already claimed by column "
                f"{claimed_at[canonical]}",
                operation=operation,
                source=source,
                worksheet=worksheet,
                column=position,
            )
        claimed_at[canonical] = position
        columns.append(canonical)

    missing = [name for name in schema.required_field_names if name not in claimed_at]
    if missing:
        raise HeaderResolutionError(
            f"required field(s) absent from the header row: {', '.join(missing)}",
            operation=operation,
            source=source,
            worksheet=worksheet,
        )

    return HeaderResolution(
        columns=tuple(columns),
        normalized=tuple(normalized),
        headers=tuple(name for name in columns if name is not None),
        unmapped=tuple(unmapped),
    )


def feed_schema(
    feed: FeedConfig,
    *,
    required_fields: Iterable[str] | None = None,
    extra_columns: ExtraColumnPolicy = ExtraColumnPolicy.RETAIN,
) -> IngestSchema:
    """Build a feed schema from configuration, as the development plan requires.

    The canonical field set is :data:`las.config.CANONICAL_FIELDS`, sorted so
    the schema is identical regardless of that frozenset's iteration order.
    Aliases come from the feed's own ``header_aliases``. ``required_fields``
    defaults to every canonical field: a *column* the schema needs is a
    structural expectation, whereas a missing *value* inside a present column
    is a business defect for the validation stage.
    """
    required = set(CANONICAL_FIELDS if required_fields is None else required_fields)
    unknown = sorted(required - set(CANONICAL_FIELDS))
    if unknown:
        raise SchemaError(f"required field(s) are not canonical: {', '.join(unknown)}")
    return IngestSchema(
        fields=tuple(
            FieldSpec(name, required=name in required) for name in sorted(CANONICAL_FIELDS)
        ),
        aliases=dict(feed.header_aliases),
        extra_columns=extra_columns,
        name=feed.name,
    )


def _master_schema(field_names: Sequence[str], name: str) -> IngestSchema:
    return IngestSchema(
        fields=tuple(FieldSpec(field_name) for field_name in field_names),
        extra_columns=ExtraColumnPolicy.REJECT,
        name=name,
    )


#: Master-data schemas. Their headers are already canonical in the committed
#: fixtures, so they declare no aliases and refuse unexpected columns.
ACCOUNT_SCHEMA = _master_schema(ACCOUNT_FIELD_NAMES, "accounts")
ACCOUNT_LINK_SCHEMA = _master_schema(ACCOUNT_LINK_FIELD_NAMES, "account_links")
