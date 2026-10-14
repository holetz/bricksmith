"""Task declaration API. Discovery lives in ``bricksmith.tasks.discovery`` and is loaded on demand."""

from bricksmith.tasks.declaration import (
    READ_METHODS,
    TaskDeclaration,
    TaskReads,
    declaration_of,
    make_declaration,
    task,
    variant_key,
)

__all__ = [
    "READ_METHODS",
    "TaskDeclaration",
    "TaskReads",
    "declaration_of",
    "make_declaration",
    "task",
    "variant_key",
]
