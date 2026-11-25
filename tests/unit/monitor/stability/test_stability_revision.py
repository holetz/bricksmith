"""Revision reliability: the declaration, the revision metrics and the first-publication snapshot."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import REVISION_LIMITS, REVISION_METRICS, parse_config
from bricksmith.monitor.stability.tables import ALL_KINDS, KEYS, REVISIONS, SCHEMAS


def test_config_revision_is_off_by_default_and_outside_serialized_forms() -> None:
    plain = parse_config({"period": "month"})
    declared = parse_config({"period": "month", "revision": True})
    assert not plain.revision and declared.revision
    assert "revision" not in plain.to_dict() and declared.to_dict()["revision"] is True
    assert plain.binning_fingerprint() == declared.binning_fingerprint()
    assert plain.to_dict()["limits"] == declared.to_dict()["limits"]


def test_config_rejects_a_non_boolean_revision() -> None:
    for bad in ("yes", 1, {"on": True}):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "revision": bad})


def test_config_revision_limits_default_and_override() -> None:
    config = parse_config({"period": "month", "revision": True})
    for metric in REVISION_METRICS:
        assert config.limits_for(metric, "x") == REVISION_LIMITS[metric]
    custom = parse_config({"period": "month", "limits": {"revision_psi": {"green": 0.01, "red": 0.02}}})
    assert custom.limits_for("revision_psi", "x") == Limits(0.01, 0.02, "higher")
    off = parse_config({"period": "month", "limits": {"revision_psi": None}})
    assert off.limits_for("revision_psi", "x") is None


def test_config_revisions_table_has_a_schema_and_a_key() -> None:
    assert REVISIONS in ALL_KINDS and REVISIONS in SCHEMAS and set(KEYS[REVISIONS]) <= set(SCHEMAS[REVISIONS])
