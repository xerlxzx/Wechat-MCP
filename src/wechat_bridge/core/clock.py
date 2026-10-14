"""UTC wall-clock timestamps in ISO-8601. Imports nothing from the package."""
from datetime import UTC, datetime


def now_utc() -> str:
    return datetime.now(UTC).isoformat()
