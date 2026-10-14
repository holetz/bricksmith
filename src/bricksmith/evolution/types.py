"""A Spark-free data type model: parsing, rendering and independent compatibility rules.

Compatibility is defined here, not borrowed from an engine: a change is a ``widen`` only when every
value of the old type is representable exactly in the new type; a ``narrow`` stays inside one numeric or
temporal family but can lose information; everything else is ``incompatible``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import NoReturn

from bricksmith.evolution.errors import SchemaSpecError

_ALIASES = {
    "tinyint": "byte",
    "smallint": "short",
    "integer": "int",
    "bigint": "long",
    "real": "float",
    "bool": "boolean",
    "varchar": "string",
    "char": "string",
}
_INTEGRALS = {"byte": 1, "short": 2, "int": 3, "long": 4}
_INTEGER_DIGITS = {"byte": 3, "short": 5, "int": 10, "long": 19}
_MAX_DECIMAL = 38
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|`(?:[^`]|``)+`")


@dataclass(frozen=True)
class Primitive:
    """A scalar type by canonical lower-case name (``int``, ``string``, ``timestamp_ntz``...)."""

    name: str

    def ddl(self) -> str:
        """Return the SQL spelling."""
        return {"byte": "TINYINT", "short": "SMALLINT", "long": "BIGINT"}.get(self.name, self.name.upper())

    def to_json(self) -> object:
        """Return the stable JSON form."""
        return self.name


@dataclass(frozen=True)
class Decimal:
    """A fixed-point decimal with precision and scale."""

    precision: int
    scale: int

    def ddl(self) -> str:
        """Return the SQL spelling."""
        return f"DECIMAL({self.precision},{self.scale})"

    def to_json(self) -> object:
        """Return the stable JSON form."""
        return f"decimal({self.precision},{self.scale})"


@dataclass(frozen=True)
class Field:
    """A named column or struct field; ``comment=None`` means the comment is not managed."""

    name: str
    type: DataType
    nullable: bool = True
    comment: str | None = None

    def to_json(self) -> dict[str, object]:
        """Return the stable JSON form."""
        out: dict[str, object] = {"name": self.name, "nullable": self.nullable, "type": self.type.to_json()}
        if self.comment is not None:
            out["comment"] = self.comment
        return out


@dataclass(frozen=True)
class Struct:
    """An ordered record of fields."""

    fields: tuple[Field, ...]

    def ddl(self) -> str:
        """Return the SQL spelling."""
        inner = ", ".join(f"`{f.name}`: {f.type.ddl()}{'' if f.nullable else ' NOT NULL'}" for f in self.fields)
        return f"STRUCT<{inner}>"

    def to_json(self) -> object:
        """Return the stable JSON form."""
        return {"struct": [f.to_json() for f in self.fields]}


@dataclass(frozen=True)
class Array:
    """A list of elements."""

    element: DataType
    contains_null: bool = True

    def ddl(self) -> str:
        """Return the SQL spelling."""
        return f"ARRAY<{self.element.ddl()}>"

    def to_json(self) -> object:
        """Return the stable JSON form."""
        return {"array": self.element.to_json(), "contains_null": self.contains_null}


@dataclass(frozen=True)
class Map:
    """A key to value mapping."""

    key: DataType
    value: DataType
    value_contains_null: bool = True

    def ddl(self) -> str:
        """Return the SQL spelling."""
        return f"MAP<{self.key.ddl()}, {self.value.ddl()}>"

    def to_json(self) -> object:
        """Return the stable JSON form."""
        return {"key": self.key.to_json(), "map": self.value.to_json(), "value_contains_null": self.value_contains_null}


DataType = Primitive | Decimal | Struct | Array | Map


def _primitive(text: str) -> DataType:
    name = text.strip().lower()
    match = re.fullmatch(r"(?:decimal|dec|numeric)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?", name)
    if match:
        precision = int(match.group(1) or 10)
        scale = int(match.group(2) or 0)
        if not 1 <= precision <= _MAX_DECIMAL or not 0 <= scale <= precision:
            msg = f"invalid decimal({precision},{scale}): precision 1..{_MAX_DECIMAL}, scale 0..precision"
            raise SchemaSpecError(msg)
        return Decimal(precision, scale)
    name = re.sub(r"\s*\(\s*\d+\s*\)$", "", name)  # varchar(n) and char(n) are strings
    return Primitive(_ALIASES.get(name, name))


class _Parser:
    def __init__(self, text: str) -> None:
        self.text, self.pos = text, 0

    def fail(self, why: str) -> NoReturn:
        msg = f"cannot parse type {self.text!r}: {why} at {self.pos}"
        raise SchemaSpecError(msg)

    def skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def eat(self, token: str) -> bool:
        self.skip()
        if self.text[self.pos : self.pos + len(token)].lower() == token.lower():
            self.pos += len(token)
            return True
        return False

    def expect(self, token: str) -> None:
        if not self.eat(token):
            self.fail(f"expected {token!r}")

    def word(self) -> str:
        self.skip()
        match = _IDENT.match(self.text, self.pos)
        if not match:
            self.fail("expected a name")
        self.pos = match.end()
        raw = match.group(0)
        return raw[1:-1].replace("``", "`") if raw.startswith("`") else raw

    def parse(self) -> DataType:
        result = self.type()
        self.skip()
        if self.pos != len(self.text):
            self.fail("trailing text")
        return result

    def type(self) -> DataType:
        self.skip()
        start = self.pos
        head = self.word().lower()
        if head == "struct":
            self.expect("<")
            fields: list[Field] = []
            while True:
                name = self.word()
                self.eat(":")
                inner = self.type()
                nullable = True
                if self.eat("NOT"):
                    self.expect("NULL")
                    nullable = False
                fields.append(Field(name, inner, nullable))
                if self.eat(","):
                    continue
                break
            self.expect(">")
            return Struct(tuple(fields))
        if head == "array":
            self.expect("<")
            element = self.type()
            self.expect(">")
            return Array(element)
        if head == "map":
            self.expect("<")
            key = self.type()
            self.expect(",")
            value = self.type()
            self.expect(">")
            return Map(key, value)
        self.pos = start
        match = re.compile(r"[A-Za-z_]+(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?").match(self.text, self.pos)
        if not match:
            self.fail("expected a type")
        self.pos = match.end()
        return _primitive(match.group(0))


def parse_type(text: str) -> DataType:
    """Parse a DDL type such as ``struct<a: int, b: array<decimal(10,2)>>``."""
    return _Parser(text).parse()


def from_spark_json(value: Any) -> DataType:  # noqa: ANN401 - a Spark type's JSON value is a string or a dict
    """Convert ``StructType.jsonValue()`` data (or any nested type of it) to the model."""
    if isinstance(value, str):
        return _primitive(value)
    kind = value.get("type")
    if kind == "struct":
        return Struct(
            tuple(
                Field(
                    f["name"],
                    from_spark_json(f["type"]),
                    bool(f.get("nullable", True)),
                    (f.get("metadata") or {}).get("comment"),
                )
                for f in value["fields"]
            )
        )
    if kind == "array":
        return Array(from_spark_json(value["elementType"]), bool(value.get("containsNull", True)))
    if kind == "map":
        return Map(
            from_spark_json(value["keyType"]),
            from_spark_json(value["valueType"]),
            bool(value.get("valueContainsNull", True)),
        )
    msg = f"unsupported Spark type description: {kind!r}"
    raise SchemaSpecError(msg)


def type_from_json(value: object) -> DataType:
    """Rebuild a type from :meth:`to_json` output or a DDL string."""
    if isinstance(value, str):
        return parse_type(value)
    if isinstance(value, dict):
        if "struct" in value:
            return Struct(tuple(field_from_json(f) for f in value["struct"]))
        if "array" in value:
            return Array(type_from_json(value["array"]), value.get("contains_null", True) is not False)
        if "map" in value:
            return Map(
                type_from_json(value["key"]),
                type_from_json(value["map"]),
                value.get("value_contains_null", True) is not False,
            )
    msg = f"unsupported type value: {value!r}"
    raise SchemaSpecError(msg)


def field_from_json(data: object) -> Field:
    """Rebuild a field from a mapping with ``name``, ``type`` and optional ``nullable``/``comment``."""
    if not isinstance(data, dict) or "name" not in data or "type" not in data:
        msg = f"a column needs name and type, got {data!r}"
        raise SchemaSpecError(msg)
    extra = set(data) - {"name", "type", "nullable", "comment"}
    if extra:
        msg = f"column {data['name']!r}: unknown keys {sorted(extra)}"
        raise SchemaSpecError(msg)
    comment = data.get("comment")
    if comment is not None and not isinstance(comment, str):
        msg = f"column {data['name']!r}: comment must be text"
        raise SchemaSpecError(msg)
    return Field(str(data["name"]), type_from_json(data["type"]), data.get("nullable", True) is not False, comment)


# --- compatibility rules -------------------------------------------------------------------------

SAME, WIDEN, NARROW, INCOMPATIBLE = "same", "widen", "narrow", "incompatible"


def _integer_fits_decimal(name: str, target: Decimal) -> bool:
    return target.precision - target.scale >= _INTEGER_DIGITS[name]


def _decimal_change(old: Decimal, new: Decimal) -> str:
    """Widen iff the new type keeps at least the old integer digits and the old fraction digits."""
    keeps_integer = new.precision - new.scale >= old.precision - old.scale
    keeps_fraction = new.scale >= old.scale
    return WIDEN if keeps_integer and keeps_fraction else NARROW


def _numeric_change(old: DataType, new: DataType) -> str | None:  # noqa: PLR0911 - the rule table
    """Return the change inside the numeric family, or ``None`` when a side is not numeric."""
    old_int = isinstance(old, Primitive) and old.name in _INTEGRALS
    new_int = isinstance(new, Primitive) and new.name in _INTEGRALS
    old_float = isinstance(old, Primitive) and old.name in {"float", "double"}
    new_float = isinstance(new, Primitive) and new.name in {"float", "double"}
    if not (old_int or old_float or isinstance(old, Decimal)):
        return None
    if not (new_int or new_float or isinstance(new, Decimal)):
        return None
    if isinstance(old, Primitive) and isinstance(new, Primitive):
        if old_int and new_int:
            return WIDEN if _INTEGRALS[old.name] < _INTEGRALS[new.name] else NARROW
        if old_float and new_float:
            return WIDEN if old.name == "float" else NARROW
        if old_int and new.name == "double":
            return WIDEN if _INTEGRALS[old.name] <= _INTEGRALS["int"] else NARROW  # long exceeds 53 bits
        if old_int and new.name == "float":
            return WIDEN if _INTEGRALS[old.name] <= _INTEGRALS["short"] else NARROW  # int exceeds 24 bits
        return NARROW  # float or double to an integral type
    if isinstance(old, Decimal) and isinstance(new, Decimal):
        return _decimal_change(old, new)
    if isinstance(new, Decimal) and isinstance(old, Primitive) and old_int:
        return WIDEN if _integer_fits_decimal(old.name, new) else NARROW
    return NARROW  # decimal to integral or floating point, or floating point to decimal


def compare_types(old: DataType, new: DataType) -> str:
    """Classify ``old`` to ``new`` for scalar types: ``same``, ``widen``, ``narrow`` or ``incompatible``."""
    if old == new:
        return SAME
    numeric = _numeric_change(old, new)
    if numeric is not None:
        return numeric
    if isinstance(old, Primitive) and isinstance(new, Primitive):
        pair = (old.name, new.name)
        if pair == ("date", "timestamp_ntz"):
            return WIDEN
        if pair in {("timestamp_ntz", "date"), ("timestamp", "date")}:
            return NARROW
    return INCOMPATIBLE
