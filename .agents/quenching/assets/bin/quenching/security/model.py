"""Read-only result shapes for the security pillar."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QuestionResult:
    """Evidence for one live security question."""

    key: str
    question: str
    state: str
    evidence: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "key": self.key,
            "question": self.question,
            "state": self.state,
            "evidence": list(self.evidence),
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class SecurityReport:
    """The complete structured answer returned by ``cq security``."""

    root: str
    questions: tuple[QuestionResult, ...]

    @property
    def refused(self) -> bool:
        return any(question.state == "refused" for question in self.questions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.refused,
            "root": self.root,
            "pillar": "security",
            "readOnly": True,
            "questions": [question.as_dict() for question in self.questions],
            "writePolicy": {"values": False, "repairFiles": False},
        }
