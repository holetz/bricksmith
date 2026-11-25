"""The stability dashboard as a native bundle resource (decision 0002: Python resources, no generated YAML).

``databricks-bundles`` (the ``bundle`` extra) is imported inside the functions that need it. Reference
:func:`load_resources` from ``databricks.yml`` under ``python: resources:`` as
``bricksmith.monitor.stability.bundle:load_resources`` and declare the bundle variables ``dataset_catalog``,
``dataset_schema`` and ``warehouse_id`` per target, or call :func:`stability_dashboard` from a resource
function of your own to choose every value.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from bricksmith.errors import MissingExtraError, ValidationError
from bricksmith.monitor.stability.dashboard import DISPLAY_NAME, dashboard_definition

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from databricks.bundles.core import Bundle, Resources  # pyright: ignore[reportMissingImports]

DEFAULT_KEY = "stability"
CATALOG_VARIABLE = "${var.dataset_catalog}"
SCHEMA_VARIABLE = "${var.dataset_schema}"
WAREHOUSE_VARIABLE = "${var.warehouse_id}"


def _require() -> None:
    try:
        import databricks.bundles  # noqa: F401, PLC0415 - presence check only  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        msg = "bundle resources need databricks-bundles; install it with: pip install 'bricksmith[bundle]'"
        raise MissingExtraError(msg) from error


def stability_dashboard(  # noqa: PLR0913 - one keyword per dashboard setting
    *,
    dataset_catalog: str,
    dataset_schema: str,
    warehouse_id: str = "",
    key: str = DEFAULT_KEY,
    display_name: str = DISPLAY_NAME,
    prefix: str = "stability_",
    parent_path: str = "",
    permissions: Sequence[Mapping[str, str]] = (),
) -> Resources:
    """Return the bundle resources holding the stability dashboard.

    ``dataset_catalog`` and ``dataset_schema`` locate the stability views and tables, and may be ``${var.*}``
    references so one definition deploys to every target. ``permissions`` are mappings such as
    ``{"level": "CAN_VIEW", "group_name": "analysts"}``.
    """
    _require()
    from databricks.bundles.core import Resources  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    from databricks.bundles.dashboards import Dashboard  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    if not dataset_catalog or not dataset_schema:
        msg = "the stability dashboard needs dataset_catalog and dataset_schema"
        raise ValidationError(msg)
    data: dict[str, Any] = {
        "display_name": display_name,
        "dataset_catalog": dataset_catalog,
        "dataset_schema": dataset_schema,
        "serialized_dashboard": json.dumps(dashboard_definition(prefix), sort_keys=True),
        "permissions": [dict(p) for p in permissions],
    }
    if warehouse_id:
        data["warehouse_id"] = warehouse_id
    if parent_path:
        data["parent_path"] = parent_path
    resources = Resources()
    resources.add_dashboard(key, Dashboard.from_dict(cast("Any", data)))
    return resources


def load_resources(bundle: Bundle) -> Resources:  # noqa: ARG001 - the signature a bundle resource loader has
    """Return the stability dashboard parameterized by the bundle variables of the target.

    Reads ``dataset_catalog``, ``dataset_schema`` and ``warehouse_id`` as ``${var.*}`` references, so each target
    sets its own values in ``databricks.yml`` and the same definition deploys everywhere.
    """
    return stability_dashboard(
        dataset_catalog=CATALOG_VARIABLE, dataset_schema=SCHEMA_VARIABLE, warehouse_id=WAREHOUSE_VARIABLE
    )


__all__ = ["load_resources", "stability_dashboard"]
