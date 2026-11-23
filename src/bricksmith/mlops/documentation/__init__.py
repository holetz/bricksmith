"""Technical model documentation, rendered deterministically from the artifacts a run persisted.

The run carries the development declaration and its exclusion composition, the selection ledger and WoE tables,
the evaluation tables, the model specification and a declared narrative (text without numbers). The document is
rendered from those alone, so regenerating from the same run yields the same bytes except the explicit
``generated_at``. Importing this package loads only the standard library; MLflow loads where it is used.
"""

from bricksmith.mlops.documentation.artifacts import (
    DOCUMENT_HTML,
    DOCUMENT_MARKDOWN,
    NARRATIVE_FIELDS,
    PATHS,
    DocumentationError,
    DocumentInputs,
    check_narrative,
    development_files,
    log_development,
    log_narrative,
    log_specification,
    read_directory,
    write_directory,
)
from bricksmith.mlops.documentation.render import Document, render_document
from bricksmith.mlops.documentation.specification import Scaling, model_specification

__all__ = [
    "DOCUMENT_HTML",
    "DOCUMENT_MARKDOWN",
    "NARRATIVE_FIELDS",
    "PATHS",
    "Document",
    "DocumentInputs",
    "DocumentationError",
    "Scaling",
    "check_narrative",
    "development_files",
    "log_development",
    "log_narrative",
    "log_specification",
    "model_specification",
    "read_directory",
    "render_document",
    "write_directory",
]
