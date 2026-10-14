"""Reproducible regression reports and the machine-readable pass/fail gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.quality.checks import FAILED, UNAVAILABLE, CheckResult
from bricksmith.quality.errors import QualityError
from bricksmith.quality.report import INVALID, ComparisonReport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

REGRESSION_VERSION = 1
PASS, FAIL = "pass", "fail"


@dataclass(frozen=True)
class GatePolicy:
    """Thresholds of the gate. The default demands identical datasets and passed checks.

    An ``invalid`` comparison (duplicate or null keys, blocked plan) always fails: it can never gate as equal.
    ``unavailable`` checks fail unless ``allow_unavailable`` is set.
    """

    max_missing_keys: int = 0
    max_extra_keys: int = 0
    max_differing_rows: int = 0
    max_differing_ratio: float | None = None
    allow_unavailable: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "allow_unavailable": self.allow_unavailable,
            "max_differing_ratio": self.max_differing_ratio,
            "max_differing_rows": self.max_differing_rows,
            "max_extra_keys": self.max_extra_keys,
            "max_missing_keys": self.max_missing_keys,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GatePolicy:
        """Build a policy from a mapping; unknown fields are rejected."""
        known = set(cls().to_dict())
        extra = sorted(set(data) - known)
        if extra:
            msg = f"unknown policy field(s): {', '.join(extra)}"
            raise QualityError(msg)
        return cls(**data)


def _gate_comparison(label: str, report: ComparisonReport, policy: GatePolicy) -> list[str]:
    if report.status == INVALID:
        return [f"{label}: comparison is invalid: {reason}" for reason in report.reasons]
    out = []
    if report.missing_in_right > policy.max_missing_keys:
        out.append(f"{label}: {report.missing_in_right} key(s) missing in right (allowed {policy.max_missing_keys})")
    if report.extra_in_right > policy.max_extra_keys:
        out.append(f"{label}: {report.extra_in_right} extra key(s) in right (allowed {policy.max_extra_keys})")
    if report.rows_with_differences > policy.max_differing_rows:
        out.append(f"{label}: {report.rows_with_differences} differing row(s) (allowed {policy.max_differing_rows})")
    if policy.max_differing_ratio is not None and report.matched_keys:
        ratio = report.rows_with_differences / report.matched_keys
        if ratio > policy.max_differing_ratio:
            out.append(f"{label}: differing ratio {ratio:.6f} above {policy.max_differing_ratio}")
    return out


@dataclass(frozen=True)
class RegressionReport:
    """Comparisons and checks of one change, the policy applied and the resulting verdict.

    The JSON form is stable and carries no timestamps or environment details, so the same inputs always
    produce the same ``fingerprint``.
    """

    name: str
    comparisons: Mapping[str, ComparisonReport]
    checks: tuple[CheckResult, ...]
    policy: GatePolicy
    failures: tuple[str, ...]

    @property
    def verdict(self) -> str:
        """``pass`` or ``fail``."""
        return FAIL if self.failures else PASS

    @property
    def exit_code(self) -> int:
        """``0`` for a pass, the documented validation exit code for a fail."""
        return EXIT_VALIDATION if self.failures else EXIT_OK

    def _body(self) -> dict[str, object]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "comparisons": {k: v.to_dict() for k, v in sorted(self.comparisons.items())},
            "failures": list(self.failures),
            "name": self.name,
            "policy": self.policy.to_dict(),
            "verdict": self.verdict,
            "version": REGRESSION_VERSION,
        }

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 of the canonical body."""
        return hashlib.sha256(json.dumps(self._body(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form including the fingerprint."""
        return {**self._body(), "fingerprint": self.fingerprint}

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RegressionReport:
        """Rebuild a report; a fingerprint that does not match the body is rejected."""
        if data.get("version") != REGRESSION_VERSION:
            msg = f"unsupported regression report version {data.get('version')!r}; expected {REGRESSION_VERSION}"
            raise QualityError(msg)
        report = cls(
            name=data["name"],
            comparisons={k: ComparisonReport.from_dict(v) for k, v in data["comparisons"].items()},
            checks=tuple(CheckResult.from_dict(c) for c in data["checks"]),
            policy=GatePolicy.from_dict(data["policy"]),
            failures=tuple(data["failures"]),
        )
        if data.get("fingerprint") != report.fingerprint:
            msg = "regression report fingerprint does not match its content"
            raise QualityError(msg)
        return report

    def to_text(self) -> str:
        """Return a short human-readable summary."""
        lines = [f"regression {self.name}: {self.verdict.upper()} ({self.fingerprint[:12]})"]
        lines += [f"  {label}: {r.status}" for label, r in sorted(self.comparisons.items())]
        lines += [f"  check {c.name}: {c.status}" + (f" ({c.reason})" if c.reason else "") for c in self.checks]
        lines += [f"  FAIL {failure}" for failure in self.failures]
        return "\n".join(lines)


def build_regression_report(
    name: str,
    *,
    comparisons: Mapping[str, ComparisonReport] | None = None,
    checks: Sequence[CheckResult] = (),
    policy: GatePolicy | None = None,
) -> RegressionReport:
    """Apply ``policy`` to labelled comparisons and check results and return the gated report."""
    chosen = policy or GatePolicy()
    compared = dict(comparisons or {})
    failures: list[str] = []
    for label, report in sorted(compared.items()):
        failures += _gate_comparison(label, report, chosen)
    for result in checks:
        if result.status == FAILED:
            failures.append(f"check {result.name}: failed: {result.reason}")
        elif result.status == UNAVAILABLE and not chosen.allow_unavailable:
            failures.append(f"check {result.name}: unavailable: {result.reason}")
    if not compared and not checks:
        failures.append("nothing was compared or checked")
    return RegressionReport(name, compared, tuple(checks), chosen, tuple(failures))
