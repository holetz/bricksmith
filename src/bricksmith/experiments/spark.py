"""Spark/Delta implementation of the experiment store (loaded only where used)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.experiments.errors import ExperimentError
from bricksmith.quality import ComparisonReport, Tolerance, compare, plan_for_frames
from bricksmith.storage.sql import quote

if TYPE_CHECKING:
    from bricksmith.context import ExecutionContext
    from bricksmith.experiments.revision import CompareSpec

HISTORY_LIMIT = 10000


class SparkExperimentStore:
    """Delta time travel, schema lifecycle and comparison over a Spark session (or a factory for one)."""

    def __init__(self, spark: Any) -> None:  # noqa: ANN401 - session or factory
        """Keep the session or factory; nothing connects here."""
        self._source = spark
        self._session: Any = None

    @classmethod
    def for_context(cls, context: ExecutionContext) -> SparkExperimentStore:
        """Build the store for ``context``; the session is created on first use."""
        from bricksmith.clients import Clients  # noqa: PLC0415 - heavy integrations load where used

        return cls(Clients(context).spark)

    @property
    def spark(self) -> Any:  # noqa: ANN401 - Spark session
        """The session, created on first use."""
        if self._session is None:
            source = self._source
            self._session = source() if callable(source) and not hasattr(source, "sql") else source
        return self._session

    def exists(self, physical: str) -> bool:
        """Whether the table exists."""
        return bool(self.spark.catalog.tableExists(physical))

    def _history(self, physical: str) -> list[Any]:
        if not self.exists(physical):
            return []
        try:
            return list(self.spark.sql(f"DESCRIBE HISTORY {quote(physical)} LIMIT {HISTORY_LIMIT}").collect())
        except Exception:  # noqa: BLE001 - views and non-Delta objects have no history
            return []

    def version(self, physical: str) -> int | None:
        """Return the latest Delta version, or None."""
        rows = self._history(physical)
        return max(int(r["version"]) for r in rows) if rows else None

    def version_at(self, physical: str, timestamp: str) -> int | None:
        """Return the newest version committed at or before the ISO ``timestamp`` (interpreted in UTC)."""
        from datetime import UTC, datetime  # noqa: PLC0415

        limit = datetime.fromisoformat(timestamp)
        limit = limit if limit.tzinfo else limit.replace(tzinfo=UTC)
        found = []
        for row in self._history(physical):
            moment = row["timestamp"]
            moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
            if moment <= limit:
                found.append(int(row["version"]))
        return max(found) if found else None

    def read_version(self, physical: str, version: int) -> object:
        """Return the table as of ``version`` as a DataFrame."""
        return self.spark.sql(f"SELECT * FROM {quote(physical)} VERSION AS OF {int(version)}")  # noqa: S608

    def schema_exists(self, catalog: str, schema: str) -> bool:
        """Whether the schema exists."""
        return bool(self.spark.catalog.databaseExists(f"{catalog}.{schema}"))

    def create_schema(self, catalog: str, schema: str) -> None:
        """Create the schema if missing."""
        self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {quote(f'{catalog}.{schema}')}")

    def drop_schema(self, catalog: str, schema: str) -> None:
        """Drop the schema with RESTRICT semantics: a schema that still holds objects is not dropped."""
        self.spark.sql(f"DROP SCHEMA IF EXISTS {quote(f'{catalog}.{schema}')} RESTRICT")

    def copy(self, source: str, target: str, *, version: int | None = None) -> None:
        """Create or replace ``target`` with ``source`` (as of ``version``)."""
        at = "" if version is None else f" VERSION AS OF {int(version)}"
        self.spark.sql(f"CREATE OR REPLACE TABLE {quote(target)} AS SELECT * FROM {quote(source)}{at}")  # noqa: S608

    def drop(self, physical: str) -> None:
        """Drop the table if it exists."""
        self.spark.sql(f"DROP TABLE IF EXISTS {quote(physical)}")

    def compare(self, left: str, right: str, spec: CompareSpec) -> ComparisonReport:
        """Compare two tables with the quality engine."""
        frames = self.spark.table(left), self.spark.table(right)
        plan = plan_for_frames(
            *frames,
            spec.keys,
            exclude=spec.exclude,
            default_tolerance=Tolerance(spec.absolute, spec.relative),
            left_name=left,
            right_name=right,
        )
        if not plan.executable:
            msg = f"comparison of {left} and {right} is not executable: " + "; ".join(
                d.message for d in plan.diagnostics
            )
            raise ExperimentError(msg)
        return compare(*frames, plan)
