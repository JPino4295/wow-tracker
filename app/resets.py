"""Pure, timezone-safe reset logic. No scheduler, no background tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import ResetConfig

UTC = timezone.utc


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp stored in the database (UTC)."""
    return as_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))


def utc_now() -> datetime:
    return datetime.now(UTC)


def period_start(
    now: datetime,
    periodicity: str,
    config: ResetConfig | None = None,
) -> datetime:
    """Return the beginning of the current daily/weekly period (UTC).

    The returned instant is always <= ``now``. Weekly periods are anchored to
    the configured weekday at the configured UTC hour.
    """
    cfg = config or ResetConfig.from_env()
    current = as_utc(now)

    if periodicity == "daily":
        start = current.replace(
            hour=cfg.daily_hour_utc, minute=0, second=0, microsecond=0
        )
        if start > current:
            start -= timedelta(days=1)
        return start

    if periodicity == "weekly":
        days_since_reset = (current.weekday() - cfg.weekly_weekday) % 7
        start = (current - timedelta(days=days_since_reset)).replace(
            hour=cfg.weekly_hour_utc, minute=0, second=0, microsecond=0
        )
        if start > current:
            start -= timedelta(days=7)
        return start

    raise ValueError(f"Unknown periodicity: {periodicity!r}")


def next_reset(
    now: datetime,
    periodicity: str,
    config: ResetConfig | None = None,
) -> datetime:
    """Return the next instant at which this periodicity resets (UTC)."""
    start = period_start(now, periodicity, config)
    return start + timedelta(days=7 if periodicity == "weekly" else 1)


def is_done(
    last_done_at: str | None,
    now: datetime,
    periodicity: str,
    config: ResetConfig | None = None,
) -> bool:
    """A progress record counts as done only inside the current period."""
    if not last_done_at:
        return False
    return parse_timestamp(last_done_at) >= period_start(now, periodicity, config)
