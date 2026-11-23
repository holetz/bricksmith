"""Today, as the ISO date every stamp is written in.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import datetime


def today() -> str:
    return datetime.date.today().isoformat()
