"""The spec document parser — everything derivable from one markdown spec, and the
writers that splice a change back into it.

Moved verbatim out of the pre-refactor specs script, cut into modules by layer: `text` (the primitives),
`sections`, `tasks`, `handoff` (the three grammars), `spec` (identity and resolution),
`derive` (the single derivation), `records` (frontmatter records and their label
projection), `edit` and `fields` (the writers).

THIS `__init__.py` CARRIES CONTENT — it is not an empty file of habit, which is the rule
`backends/__init__.py` set when it took the factory. The content is the package's public
surface: `backends/*.py` import the public names from `quenching.specs.parse`, not
from the submodule each happens to sit in today, so the layer boundary is this module and
the cut inside it stays free to move."""
from __future__ import annotations

from quenching.specs.parse.derive import board_state_of, derive_info
from quenching.specs.parse.fields import (FIELD_KEYS, carry_forward_fields,
                                          strip_frontmatter_keys)
from quenching.specs.parse.records import (declared_tags, derive_labels, reconcile_label_set,
                                           tags_outside_catalog)
from quenching.specs.parse.spec import PHASES, resolve_one, spec_handle

__all__ = [
    "FIELD_KEYS",
    "PHASES",
    "board_state_of",
    "carry_forward_fields",
    "declared_tags",
    "derive_info",
    "derive_labels",
    "reconcile_label_set",
    "resolve_one",
    "spec_handle",
    "strip_frontmatter_keys",
    "tags_outside_catalog",
]
