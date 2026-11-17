"""Reset boundaries: the easiest place to get timezones wrong."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import ResetConfig
from app.resets import as_utc, is_done, next_reset, parse_timestamp, period_start

UTC = timezone.utc
DEFAULT = ResetConfig(daily_hour_utc=7, weekly_weekday=2, weekly_hour_utc=7)


@pytest.mark.parametrize(
    ("moment", "expected_start"),
    [
        (datetime(2026, 11, 17, 6, 59, 59), datetime(2026, 11, 16, 7, 0)),
        (datetime(2026, 11, 17, 7, 0, 0), datetime(2026, 11, 17, 7, 0)),
        (datetime(2026, 11, 17, 7, 0, 1), datetime(2026, 11, 17, 7, 0)),
        (datetime(2026, 11, 17, 23, 59, 59), datetime(2026, 11, 17, 7, 0)),
    ],
)
def test_daily_period_starts_at_configured_utc_hour(
    moment: datetime, expected_start: datetime
) -> None:
    assert period_start(moment, "daily", DEFAULT) == expected_start.replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected_start"),
    [
        # Monday and Tuesday before Wednesday reset: still last week's period.
        (datetime(2026, 11, 16, 23, 0), datetime(2026, 11, 11, 7, 0)),
        (datetime(2026, 11, 17, 6, 59, 59), datetime(2026, 11, 11, 7, 0)),
        # Wednesday just before and exactly at reset.
        (datetime(2026, 11, 18, 6, 59, 59), datetime(2026, 11, 11, 7, 0)),
        (datetime(2026, 11, 18, 7, 0), datetime(2026, 11, 18, 7, 0)),
        # Rest of the week stays in the same period.
        (datetime(2026, 11, 19, 12, 0), datetime(2026, 11, 18, 7, 0)),
        (datetime(2026, 11, 24, 23, 59), datetime(2026, 11, 18, 7, 0)),
        # Following Wednesday resets again.
        (datetime(2026, 11, 25, 7, 1), datetime(2026, 11, 25, 7, 0)),
    ],
)
def test_weekly_period_is_anchored_to_wednesday_0700_utc(
    moment: datetime, expected_start: datetime
) -> None:
    assert period_start(moment, "weekly", DEFAULT) == expected_start.replace(tzinfo=UTC)


def test_aware_datetimes_are_converted_to_utc_before_computing() -> None:
    # 08:30 in UTC+2 == 06:30 UTC, i.e. before the 07:00 reset.
    moment = datetime(2026, 11, 17, 8, 30, tzinfo=timezone(timedelta(hours=2)))
    assert period_start(moment, "daily", DEFAULT) == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert period_start(moment, "weekly", DEFAULT) == datetime(2026, 11, 11, 7, 0, tzinfo=UTC)


def test_naive_datetimes_are_treated_as_utc() -> None:
    moment = datetime(2026, 11, 17, 6, 30)
    assert as_utc(moment) == moment.replace(tzinfo=UTC)
    assert period_start(moment, "daily", DEFAULT) == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert period_start(moment, "weekly", DEFAULT) == datetime(2026, 11, 11, 7, 0, tzinfo=UTC)


def test_custom_reset_constants_change_the_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_RESET_HOUR_UTC", "15")
    monkeypatch.setenv("WEEKLY_RESET_WEEKDAY", "0")  # Monday
    monkeypatch.setenv("WEEKLY_RESET_HOUR_UTC", "8")
    config = ResetConfig.from_env()
    assert config.daily_hour_utc == 15
    assert config.weekly_weekday == 0
    assert config.weekly_hour_utc == 8

    # Tuesday 07:30: daily belongs to previous day (15:00), weekly to Monday 08:00.
    moment = datetime(2026, 11, 17, 7, 30, tzinfo=UTC)
    assert period_start(moment, "daily", config) == datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    assert period_start(moment, "weekly", config) == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def test_invalid_environment_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_RESET_HOUR_UTC", "25")
    with pytest.raises(ValueError, match="DAILY_RESET_HOUR_UTC"):
        ResetConfig.from_env()


@pytest.mark.parametrize(
    ("last_done_at", "expected"),
    [
        (None, False),
        ("2026-11-16T07:00:00+00:00", False),
        ("2026-11-17T06:59:59+00:00", False),
        ("2026-11-17T07:00:00+00:00", True),
        ("2026-11-17T12:00:00Z", True),
    ],
)
def test_is_done_for_daily_progress(last_done_at: str | None, expected: bool) -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert is_done(last_done_at, now, "daily", DEFAULT) is expected


@pytest.mark.parametrize(
    ("last_done_at", "expected"),
    [
        ("2026-11-11T07:00:00+00:00", False),
        ("2026-11-17T07:00:00+00:00", False),
        ("2026-11-18T07:00:00+00:00", True),
        ("2026-11-22T23:00:00+00:00", True),
    ],
)
def test_is_done_for_weekly_progress(last_done_at: str | None, expected: bool) -> None:
    now = datetime(2026, 11, 22, 23, 30, tzinfo=UTC)
    assert is_done(last_done_at, now, "weekly", DEFAULT) is expected


def test_next_reset_is_one_period_after_current_start() -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert next_reset(now, "daily", DEFAULT) == datetime(2026, 11, 18, 7, 0, tzinfo=UTC)
    assert next_reset(now, "weekly", DEFAULT) == datetime(2026, 11, 18, 7, 0, tzinfo=UTC)


def test_parse_timestamp_accepts_both_z_and_offset() -> None:
    assert parse_timestamp("2026-11-17T07:00:00Z") == datetime(2026, 11, 17, 7, 0, tzinfo=UTC)
    assert parse_timestamp("2026-11-17T09:00:00+02:00") == datetime(2026, 11, 17, 7, 0, tzinfo=UTC)


def test_unknown_periodicity_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown periodicity"):
        period_start(datetime(2026, 11, 17, tzinfo=UTC), "hourly", DEFAULT)
