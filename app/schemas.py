"""Pydantic models used at the HTTP boundary."""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class ItemType(str, Enum):
    mount = "mount"
    transmog = "transmog"
    toy = "toy"
    pet = "pet"
    other = "other"


class Periodicity(str, Enum):
    daily = "daily"
    weekly = "weekly"
    once = "once"


class SourceKind(str, Enum):
    raid = "raid"
    rare = "rare"
    boss = "boss"
    secret = "secret"
    farming = "farming"
    dungeon = "dungeon"


def _http_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be an http or https URL")
    return cleaned


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: SourceKind
    zone: str = Field(default="", max_length=200)
    wowhead_url: str = Field(default="", max_length=1000)

    _validate_url = field_validator("wowhead_url")(_http_url)


class SourceOut(BaseModel):
    id: int
    name: str
    kind: SourceKind
    zone: str
    wowhead_url: str


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: SourceKind | None = None
    zone: str | None = Field(default=None, max_length=200)
    wowhead_url: str | None = Field(default=None, max_length=1000)

    @field_validator("wowhead_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return None if value is None else _http_url(value)

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> SourceUpdate:
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ItemSourceCreate(BaseModel):
    source_id: int
    drop_rate: float | None = Field(default=None, ge=0, le=100)
    difficulty: str = Field(default="", max_length=100)
    conditions: str = Field(default="", max_length=2000)


class ItemSourceOut(BaseModel):
    source_id: int
    source_name: str
    source_kind: SourceKind
    zone: str
    wowhead_url: str
    drop_rate: float | None
    difficulty: str
    conditions: str


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    wowhead_id: int | None = Field(default=None, ge=1)
    type: ItemType
    icon_local: str = Field(default="", max_length=300)
    periodicity: Periodicity
    owner_user_id: int | None = Field(default=None, ge=1)
    url_wowhead: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=5000)
    notes: str = Field(default="", max_length=10000)
    last_scraped_at: str | None = None
    sources: list[ItemSourceCreate] = Field(default_factory=list)

    _validate_url = field_validator("url_wowhead")(_http_url)

    @model_validator(mode="after")
    def fill_wowhead_url(self) -> ItemCreate:
        if not self.url_wowhead and self.wowhead_id is not None:
            self.url_wowhead = f"https://www.wowhead.com/es/item={self.wowhead_id}"
        return self


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    wowhead_id: int | None = Field(default=None, ge=1)
    type: ItemType | None = None
    icon_local: str | None = Field(default=None, max_length=300)
    periodicity: Periodicity | None = None
    owner_user_id: int | None = Field(default=None, ge=1)
    url_wowhead: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=10000)
    last_scraped_at: str | None = None
    sources: list[ItemSourceCreate] | None = None

    @field_validator("url_wowhead")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return None if value is None else _http_url(value)


class ProgressUpdate(BaseModel):
    done: bool


class ProgressOut(BaseModel):
    done: bool
    last_done_at: str | None


class ItemOut(BaseModel):
    id: int
    wowhead_id: int | None
    name: str
    type: ItemType
    icon_local: str
    periodicity: Periodicity
    owner_user_id: int | None
    url_wowhead: str
    description: str
    notes: str
    last_scraped_at: str | None
    created_at: str
    updated_at: str
    next_reset_at: str
    sources: list[ItemSourceOut]
    progress: dict[str, ProgressOut]


class UserOut(BaseModel):
    id: int
    name: str


class UserUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class MetaOut(BaseModel):
    users: list[UserOut]
    daily_hour_utc: int
    weekly_weekday: int
    weekly_hour_utc: int
