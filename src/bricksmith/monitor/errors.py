"""Errors of the monitoring capability."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class MonitorError(ValidationError):
    """A reference, bin definition, rule or input cannot be used; raised before any metric is invented."""

    code = "monitor_invalid"
