"""Errors of the feature-engineering capability."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class FeatureError(ValidationError):
    """A feature spec or its input cannot be used; raised before any feature is computed."""

    code = "feature_spec"


class PointInTimeError(FeatureError):
    """The input holds rows after the requested closing, which would leak the future into the features."""

    code = "point_in_time"


class UnsupportedAggregateError(FeatureError):
    """The chosen engine cannot compute an aggregate of the spec."""

    code = "unsupported_aggregate"
