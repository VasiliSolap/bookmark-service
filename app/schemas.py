from datetime import datetime

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from .urls import validate_url

MAX_TITLE_LENGTH = 200


class BookmarkCreate(BaseModel):
    # Unknown fields are rejected so a typo like "ulr" is reported, not ignored.
    model_config = ConfigDict(extra="forbid")

    # StrictStr: a number or a list is rejected instead of being coerced to "123".
    url: StrictStr
    title: StrictStr | None = None

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return validate_url(value)

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_TITLE_LENGTH:
            raise ValueError(f"must be at most {MAX_TITLE_LENGTH} characters")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("must not contain control characters")
        return value or None


class Bookmark(BaseModel):
    id: int
    url: str
    title: str | None
    created_at: datetime


class BookmarkList(BaseModel):
    items: list[Bookmark]
    next_cursor: int | None
