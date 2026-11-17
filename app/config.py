"""Environment-driven configuration for the tracker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Directory mounted as a Docker volume (database, images, backups)."""
    return Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).expanduser().resolve()


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH", data_dir() / "tracker.db")).expanduser().resolve()


def images_dir() -> Path:
    return (data_dir() / "images").resolve()


def backups_dir() -> Path:
    return (data_dir() / "backups").resolve()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as err:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from err
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


@dataclass(frozen=True)
class ResetConfig:
    """Reset moments expressed in UTC, so daylight saving never shifts them."""

    daily_hour_utc: int = 7
    weekly_weekday: int = 1  # 0 = Monday, 1 = Tuesday
    weekly_hour_utc: int = 7

    @classmethod
    def from_env(cls) -> "ResetConfig":
        return cls(
            daily_hour_utc=_env_int("DAILY_RESET_HOUR_UTC", 7, 0, 23),
            weekly_weekday=_env_int("WEEKLY_RESET_WEEKDAY", 1, 0, 6),
            weekly_hour_utc=_env_int("WEEKLY_RESET_HOUR_UTC", 7, 0, 23),
        )


def user_names() -> tuple[str, str]:
    first = os.environ.get("USER_1_NAME", "Jugador 1").strip() or "Jugador 1"
    second = os.environ.get("USER_2_NAME", "Jugador 2").strip() or "Jugador 2"
    return first, second
