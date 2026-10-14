"""Operational observability and cost controls: events, reports, readiness, alerts and budgets."""

from bricksmith.ops.adapters import FileAdapter, LogAdapter, WebhookAdapter
from bricksmith.ops.budgets import Budgets, DryRunExplanation, OpsConfig, TaskEstimate, explain_plan, load_ops_config
from bricksmith.ops.errors import BudgetExceededError, OpsConfigError, StatisticalAlertError
from bricksmith.ops.events import (
    ERROR_CLASSES,
    Event,
    EventSink,
    JsonLinesSink,
    ListSink,
    LoggingSink,
    RunEvents,
    classify_error,
)
from bricksmith.ops.notify import (
    AlertEvent,
    AlertNotifier,
    DeliveryRecord,
    InMemoryAlertStore,
    JsonFileAlertStore,
    alerts_from_report,
    notify_report,
)
from bricksmith.ops.readiness import (
    InputSpec,
    ReadinessReport,
    TableInspector,
    TableStats,
    evaluate_completeness,
    evaluate_freshness,
    evaluate_readiness,
)
from bricksmith.ops.redact import redact_text, redact_value
from bricksmith.ops.report import (
    Diagnosis,
    build_ops_report,
    diagnose,
    recovery_guidance,
    render_ops_report,
    resource_usage,
)

__all__ = [
    "ERROR_CLASSES",
    "AlertEvent",
    "AlertNotifier",
    "BudgetExceededError",
    "Budgets",
    "DeliveryRecord",
    "Diagnosis",
    "DryRunExplanation",
    "Event",
    "EventSink",
    "FileAdapter",
    "InMemoryAlertStore",
    "InputSpec",
    "JsonFileAlertStore",
    "JsonLinesSink",
    "ListSink",
    "LogAdapter",
    "LoggingSink",
    "OpsConfig",
    "OpsConfigError",
    "ReadinessReport",
    "RunEvents",
    "StatisticalAlertError",
    "TableInspector",
    "TableStats",
    "TaskEstimate",
    "WebhookAdapter",
    "alerts_from_report",
    "build_ops_report",
    "classify_error",
    "diagnose",
    "evaluate_completeness",
    "evaluate_freshness",
    "evaluate_readiness",
    "explain_plan",
    "load_ops_config",
    "notify_report",
    "recovery_guidance",
    "redact_text",
    "redact_value",
    "render_ops_report",
    "resource_usage",
]
