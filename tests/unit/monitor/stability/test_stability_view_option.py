"""The opt-in ``views`` declaration key and the creation of the dashboard views after a run."""

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.hook import Declaration, create_views, resolve
from bricksmith.monitor.stability.tables import view_statements

OUTPUTS = {"orders": "main.sales.orders"}


class _Spark:
    def __init__(self, fail_on: str = "") -> None:
        self.statements: list[str] = []
        self.fail_on = fail_on

    def sql(self, statement: str) -> None:
        if self.fail_on and self.fail_on in statement:
            msg = "no such table"
            raise RuntimeError(msg)
        self.statements.append(statement)


def _declaration(**extra: object) -> Declaration:
    return resolve({"period": "month", **extra}, OUTPUTS)[0]


def test_views_is_off_by_default_and_serialized_only_when_on() -> None:
    assert not parse_config({"period": "month"}).views
    assert "views" not in parse_config({"period": "month"}).to_dict()
    assert parse_config({"period": "month", "views": True}).to_dict()["views"] is True


def test_views_must_be_true_or_false() -> None:
    with pytest.raises(MonitorError):
        parse_config({"period": "month", "views": "yes"})


def test_views_do_not_change_the_binning() -> None:
    assert (
        parse_config({"period": "month", "views": True}).binning_fingerprint()
        == parse_config({"period": "month"}).binning_fingerprint()
    )


def test_a_declaration_with_views_creates_every_view_in_its_scope() -> None:
    spark = _Spark()
    assert create_views(spark, _declaration(views=True)) == []
    assert spark.statements == view_statements("main.sales")


def test_the_declared_scope_and_prefix_name_the_views() -> None:
    spark = _Spark()
    create_views(spark, _declaration(views=True, scope="main.monitoring", prefix="st_"))
    assert all("main.monitoring.st_" in s for s in spark.statements)


def test_without_the_option_or_a_session_nothing_is_created() -> None:
    spark = _Spark()
    assert create_views(spark, _declaration()) == []
    assert create_views(None, _declaration(views=True)) == []
    assert spark.statements == []


def test_a_failing_view_is_reported_and_the_others_are_still_created() -> None:
    spark = _Spark(fail_on="stability_series")
    errors = create_views(spark, _declaration(views=True))
    assert len(errors) == 1
    assert "main.sales.stability_series" in errors[0]
    assert len(spark.statements) == len(view_statements("main.sales")) - 1
