"""Pure type rules and the declared-versus-live diff, on synthetic schemas."""
# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

import pytest

from bricksmith.evolution import TableSchema, compare_types, diff_schemas, parse_type
from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.evolution.model import normalize_expression, references, schema_from_spark, substitute


def schema(*cols: tuple[str, str] | tuple[str, str, bool], **meta: object) -> TableSchema:
    columns = [{"name": c[0], "type": c[1], "nullable": c[2] if len(c) == 3 else True} for c in cols]
    return TableSchema.from_dict({"columns": columns, **meta})


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("int", "int", "same"),
        ("tinyint", "smallint", "widen"),
        ("smallint", "int", "widen"),
        ("int", "bigint", "widen"),
        ("bigint", "int", "narrow"),
        ("float", "double", "widen"),
        ("double", "float", "narrow"),
        ("int", "double", "widen"),
        ("bigint", "double", "narrow"),
        ("smallint", "float", "widen"),
        ("int", "float", "narrow"),
        ("double", "bigint", "narrow"),
        ("int", "decimal(10,0)", "widen"),
        ("int", "decimal(9,0)", "narrow"),
        ("bigint", "decimal(20,0)", "widen"),
        ("bigint", "decimal(18,0)", "narrow"),
        ("decimal(10,2)", "decimal(12,2)", "widen"),
        ("decimal(10,2)", "decimal(12,4)", "widen"),
        ("decimal(10,2)", "decimal(10,4)", "narrow"),  # fewer integer digits
        ("decimal(10,2)", "decimal(9,2)", "narrow"),
        ("decimal(10,2)", "decimal(12,1)", "narrow"),  # fewer fraction digits
        ("decimal(10,2)", "bigint", "narrow"),
        ("decimal(10,2)", "double", "narrow"),
        ("date", "timestamp_ntz", "widen"),
        ("timestamp", "date", "narrow"),
        ("timestamp", "timestamp_ntz", "incompatible"),
        ("string", "int", "incompatible"),
        ("int", "string", "incompatible"),
        ("boolean", "int", "incompatible"),
        ("binary", "string", "incompatible"),
    ],
)
def test_classification_table(old: str, new: str, expected: str) -> None:
    assert compare_types(parse_type(old), parse_type(new)) == expected


def test_decimal_defaults_and_validation() -> None:
    assert parse_type("decimal") == parse_type("decimal(10,0)")
    assert parse_type("numeric(5)") == parse_type("decimal(5,0)")
    for bad in ("decimal(39,0)", "decimal(5,6)", "decimal(0,0)"):
        with pytest.raises(SchemaSpecError):
            parse_type(bad)


def test_nested_type_parse_and_render() -> None:
    parsed = parse_type("struct<a: int NOT NULL, b: array<struct<c: decimal(8,2)>>, d: map<string, long>>")
    assert parsed.ddl() == "STRUCT<`a`: INT NOT NULL, `b`: ARRAY<STRUCT<`c`: DECIMAL(8,2)>>, `d`: MAP<STRING, BIGINT>>"
    with pytest.raises(SchemaSpecError):
        parse_type("struct<a int")


def test_diff_nested_types_nullability_and_comments() -> None:
    live = TableSchema.from_dict(
        {
            "columns": [
                {"name": "id", "type": "int", "nullable": False},
                {"name": "addr", "type": "struct<zip: int, city: string>"},
                {"name": "tags", "type": "array<struct<k: string>>"},
                {"name": "m", "type": "map<string, int>", "comment": "old"},
            ]
        }
    )
    declared = TableSchema.from_dict(
        {
            "columns": [
                {"name": "id", "type": "int"},
                {"name": "addr", "type": "struct<zip: bigint, city: string, geo: double>"},
                {"name": "tags", "type": "array<struct<k: string, v: int>>"},
                {"name": "m", "type": "map<string, bigint>", "comment": "new"},
            ]
        }
    )
    got = {(c.kind, c.path): (c.before, c.after) for c in diff_schemas(declared, live)}
    assert got == {
        ("set_nullable", "id"): (False, True),
        ("change_type", "addr.zip"): ("INT", "BIGINT"),
        ("add_column", "addr.geo"): (None, "DOUBLE"),
        ("add_column", "tags.element.v"): (None, "INT"),
        ("set_comment", "m"): ("old", "new"),
        ("change_type", "m.value"): ("INT", "BIGINT"),
    }
    assert diff_schemas(live, live) == []


def test_diff_checks_keys_properties_and_unmanaged_aspects() -> None:
    live = schema(
        ("id", "int", False),
        ("amount", "int"),
        checks={"pos": "amount > 0", "extra": "id > 0"},
        properties={"a": "1", "z": "9"},
    )
    declared = schema(
        ("id", "int", False),
        ("amount", "int"),
        checks={"pos": "(AMOUNT>0)", "new": "amount < 9"},
        primary_key=("id",),
        properties={"a": "2"},
    )
    got = [(c.kind, c.path) for c in diff_schemas(declared, live)]
    # "pos" differs only in case and parentheses/spacing is not equal here (no spaces), "extra"/"z" are unmanaged
    assert ("add_check", "new") in got and ("set_key", "primary_key") in got and ("set_property", "a") in got
    assert not any(p in {"extra", "z"} for _, p in got)
    assert normalize_expression("( Amount  >  0 )") == normalize_expression("amount > 0")


def test_renames_are_declared_not_inferred_and_reruns_are_noops() -> None:
    live = schema(("id", "int"), ("old_name", "string"))
    declared = schema(("id", "int"), ("new_name", "string"))
    kinds = sorted(c.kind for c in diff_schemas(declared, live))
    assert kinds == ["add_column", "drop_column"]
    renamed = [c.kind for c in diff_schemas(declared, live, {"old_name": "new_name"})]
    assert renamed == ["rename_column"]
    assert diff_schemas(declared, declared, {"old_name": "new_name"}) == []
    with pytest.raises(SchemaSpecError):
        diff_schemas(declared, live, {"ghost": "new_name"})


def test_identifier_references() -> None:
    assert references("`amount` > 0 AND x.amount_total > 1", "amount")
    assert not references("amount_total > 0", "amount")
    assert substitute("amount > 0 and amount_total > 1", "amount", "value") == "value > 0 and amount_total > 1"


def test_live_model_from_spark_json_splits_checks_from_properties() -> None:
    raw = {
        "type": "struct",
        "fields": [
            {"name": "a", "type": "decimal(10,2)", "nullable": False, "metadata": {"comment": "c"}},
            {
                "name": "b",
                "type": {"type": "array", "elementType": "string", "containsNull": True},
                "nullable": True,
                "metadata": {},
            },
        ],
    }
    live = schema_from_spark(raw, properties={"delta.constraints.pos": "a > 0", "k": "v"}, comment="t")
    assert live.checks == {"pos": "a > 0"} and live.properties == {"k": "v"} and live.comment == "t"
    assert live.columns[0].comment == "c" and not live.columns[0].nullable
    assert live.to_dict() == TableSchema.from_dict(live.to_dict()).to_dict()
