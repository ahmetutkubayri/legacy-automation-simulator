"""Read-only ingestion of CSV and XLSX sources into canonical rows.

Depends only on the standard library, the pinned ``openpyxl``, and the
project's configuration models (from which feed aliases are read). It knows
nothing of the simulator, the driver, or any later pipeline stage — those
depend on ingestion, never the reverse.

Ingestion decides only whether a *file* is structurally sound. Whether its
*contents* are correct is the validation stage's question, so business-invalid
values survive ingestion untouched.
"""

from las.ingest.errors import (
    FormulaCellError,
    HeaderResolutionError,
    IngestError,
    MalformedRowError,
    SchemaError,
    SourceLabelError,
    SourceReadError,
    UnsupportedCellValueError,
    UnsupportedFormatError,
    WorksheetSelectionError,
)
from las.ingest.headers import (
    ACCOUNT_FIELD_NAMES,
    ACCOUNT_LINK_FIELD_NAMES,
    ACCOUNT_LINK_SCHEMA,
    ACCOUNT_SCHEMA,
    SEPARATOR_CHARACTERS,
    ExtraColumnPolicy,
    FieldSpec,
    HeaderResolution,
    IngestSchema,
    feed_schema,
    normalize_header,
    resolve_headers,
)
from las.ingest.models import (
    MAX_SOURCE_LABEL_LENGTH,
    IngestedRow,
    IngestOutcome,
    IngestResult,
    RowProvenance,
    SourceFormat,
    safe_source_label,
)
from las.ingest.readers import (
    DEFAULT_ENCODINGS,
    DELIMITER_CANDIDATES,
    infer_delimiter,
    read_csv,
    read_source,
    read_xlsx,
)
from las.ingest.values import FALSE_TEXT, TRUE_TEXT, cell_to_text

__all__ = [
    "ACCOUNT_FIELD_NAMES",
    "ACCOUNT_LINK_FIELD_NAMES",
    "ACCOUNT_LINK_SCHEMA",
    "ACCOUNT_SCHEMA",
    "DEFAULT_ENCODINGS",
    "DELIMITER_CANDIDATES",
    "FALSE_TEXT",
    "MAX_SOURCE_LABEL_LENGTH",
    "SEPARATOR_CHARACTERS",
    "TRUE_TEXT",
    "ExtraColumnPolicy",
    "FieldSpec",
    "FormulaCellError",
    "HeaderResolution",
    "HeaderResolutionError",
    "IngestError",
    "IngestOutcome",
    "IngestResult",
    "IngestSchema",
    "IngestedRow",
    "MalformedRowError",
    "RowProvenance",
    "SchemaError",
    "SourceFormat",
    "SourceLabelError",
    "SourceReadError",
    "UnsupportedCellValueError",
    "UnsupportedFormatError",
    "WorksheetSelectionError",
    "cell_to_text",
    "feed_schema",
    "infer_delimiter",
    "normalize_header",
    "read_csv",
    "read_source",
    "read_xlsx",
    "resolve_headers",
    "safe_source_label",
]
