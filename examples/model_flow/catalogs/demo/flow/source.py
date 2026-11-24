"""The periodic source of the model flow: one synthetic row per account and monthly closing.

Accounts belong to customers (two accounts per customer). Some accounts close during the history; a few closings
before they close, their balance falls and their days past due rise, so windowed features can see it coming.
Everything is generated from a fixed seed: the same rows on every run, nothing read from a clock.
"""

import calendar
import random
from datetime import date

from bricksmith.tasks import task

ACCOUNTS = 240
SEGMENTS = ("standard", "plus", "premium")
FIRST_YEAR, MONTHS = 2024, 24
DDL = (
    "account_id BIGINT, customer_id BIGINT, closing_date DATE, balance DOUBLE, payments DOUBLE, "
    "days_past_due DOUBLE, status STRING, segment STRING, closed BOOLEAN, tenure_months INT"
)


def closings():
    """Return the month-end closings of the history, oldest first."""
    out = []
    for index in range(MONTHS):
        year, month = FIRST_YEAR + index // 12, index % 12 + 1
        out.append(date(year, month, calendar.monthrange(year, month)[1]))
    return out


def source_rows():
    """Return the synthetic history as tuples in ``DDL`` order (the same rows on every call)."""
    rng = random.Random(84)  # noqa: S311 - synthetic data, not a secret
    days = closings()
    rows = []
    for account in range(1, ACCOUNTS + 1):
        risk = rng.random()
        opened = rng.randrange(-24, 9)  # index of the opening closing; negative means opened before the history
        close_at = rng.randrange(8, MONTHS) if risk > 0.55 and rng.random() < risk else None  # noqa: PLR2004
        balance = 1000.0 + 4000.0 * rng.random()
        segment = SEGMENTS[account % len(SEGMENTS)]
        for index, closing in enumerate(days):
            if index < opened:
                continue
            closed = close_at is not None and index >= close_at
            stress = 4 if closed else max(0, 4 - (close_at - index)) if close_at is not None else 0
            balance = 0.0 if closed else max(0.0, balance * (1.0 + rng.gauss(0.01, 0.03) - 0.06 * stress * risk))
            payments = None if rng.random() < 0.03 else round(balance * (0.05 + 0.05 * rng.random()), 2)  # noqa: PLR2004
            past_due = 0.0 if closed else float(min(120, max(0, round(rng.gauss(8.0 * stress * risk, 3.0)))))
            status = "late" if past_due > 0 else "paid"
            rows.append(
                (account, (account + 1) // 2, closing, round(balance, 2), payments, past_due, status, segment,
                 closed, index - opened)
            )  # fmt: skip
    return rows


@task(kind="table", outputs="accounts_monthly", metadata={"write": "overwrite"})
def accounts_monthly(ctx):
    """Write the synthetic monthly history."""
    ctx.write("accounts_monthly", ctx.io.spark.createDataFrame(source_rows(), DDL))
