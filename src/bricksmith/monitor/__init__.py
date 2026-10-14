"""Data and model monitoring: versioned bin references, PSI/CSI, discrimination, calibration and alert rules.

Computation is separate from persistence. ``measure`` works on lists of mappings (pure Python) or Spark
DataFrames (aggregated remotely) with identical results; inside a project, ordinary ``monitor`` tasks write
the results (see ``bricksmith.monitor.persist``). The root import is light: Spark loads where it is used.
"""

from bricksmith.monitor.adapter import MonitorReport, fit_reference, measure, monitor_frames
from bricksmith.monitor.alerts import Evaluation, alert_events, evaluate
from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.measure import Measurement, Summary
from bricksmith.monitor.persist import evaluate_stored, load_reference, measurement_rows, reference_row, to_frame
from bricksmith.monitor.reference import AlertRule, Reference, resolve_window

__all__ = [
    "AlertRule",
    "BinSpec",
    "Evaluation",
    "Measurement",
    "MonitorError",
    "MonitorReport",
    "Reference",
    "Summary",
    "alert_events",
    "evaluate",
    "evaluate_stored",
    "fit_reference",
    "load_reference",
    "measure",
    "measurement_rows",
    "monitor_frames",
    "reference_row",
    "resolve_window",
    "to_frame",
]
