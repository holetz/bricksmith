"""Model monitoring declared on tasks: score and feature drift, discrimination, calibration, volume and completeness.

A task that writes predictions declares ``metadata={"model_monitoring": {"model": ..., "period": ..., ...}}``;
the plan lists the model monitoring tables as declared targets and the shared runner measures the written output
period by period against its development (and out-of-time) reference, colors each result, writes the results,
details, alerts and a run record (last), and applies the declared gate. ``bricksmith.monitor.model.engine.check``
runs the same measurement on any rows or DataFrame. Spark loads only where it is used.
"""

from bricksmith.monitor.model.config import (
    FAMILIES,
    KEY,
    Gate,
    ModelMonitorConfig,
    Sample,
    parse_config,
    parse_declarations,
)

__all__ = ["FAMILIES", "KEY", "Gate", "ModelMonitorConfig", "Sample", "parse_config", "parse_declarations"]
