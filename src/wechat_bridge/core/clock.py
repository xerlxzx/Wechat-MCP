"""UTC wall-clock timestamps in ISO-8601. Imports nothing from the package."""
from datetime import UTC, datetime


def now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()
