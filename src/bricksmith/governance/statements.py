"""Pure SQL rendering for governance controls; no I/O."""

from __future__ import annotations

from bricksmith.governance.errors import GovernanceConfigError
from bricksmith.governance.model import COMMENT, GRANT, PROPERTY, TAG, Control
from bricksmith.storage.sql import literal, quote

_KEYWORD = {"table": "TABLE", "view": "VIEW", "schema": "SCHEMA", "catalog": "CATALOG"}
_PROPERTIES = {"table": "ALTER TABLE {n} SET TBLPROPERTIES ({k} = {v})",
               "view": "ALTER VIEW {n} SET TBLPROPERTIES ({k} = {v})"}  # fmt: skip


def ident(name: str) -> str:
    """Backtick-quote one identifier (principals may contain dots or spaces)."""
    return "`" + name.replace("`", "``") + "`"


def render(control: Control, kind: str) -> str:
    """Return the statement that sets ``control`` on a resource of ``kind``; masks and filters have none."""
    name, keyword = quote(control.resource), _KEYWORD[kind]
    if control.kind == TAG:
        return f"ALTER {keyword} {name} SET TAGS ({literal(control.key)} = {literal(control.value)})"
    if control.kind == GRANT:
        principal, _, privilege = control.key.rpartition(":")
        return f"GRANT {privilege} ON {keyword} {name} TO {ident(principal)}"
    if control.kind == COMMENT:
        return f"COMMENT ON {'TABLE' if kind == 'view' else keyword} {name} IS {literal(control.value)}"
    if control.kind == PROPERTY and kind in _PROPERTIES:
        return _PROPERTIES[kind].format(n=name, k=literal(control.key), v=literal(control.value))
    msg = f"no statement exists for {control.kind} controls on a {kind}"
    raise GovernanceConfigError(msg)
