import hashlib
import ipaddress
import json
import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

TERMINAL = ("succeeded", "partial", "failed", "cancelled")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def validate_url(value: str) -> str:
    if len(value) > 8192 or any(ord(c) < 33 for c in value):
        raise ValueError("URL_INVALID")
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ValueError("URL_INVALID")
    if host.lower() == "localhost" or host.lower().endswith((".local", ".localhost", ".internal")):
        raise ValueError("URL_LOCAL_ADDRESS")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or "." not in host:
            raise ValueError("URL_INVALID") from None
    else:
        if not address.is_global:
            raise ValueError("URL_LOCAL_ADDRESS")
    return value


def provider(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for canonical, aliases in {
        "youtube": ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"),
        "twitter": ("x.com", "twitter.com", "mobile.twitter.com", "t.co"),
        "pixiv": ("pixiv.net",),
        "instagram": ("instagram.com",),
        "reddit": ("reddit.com", "redd.it"),
    }.items():
        if any(host == alias or host.endswith("." + alias) for alias in aliases):
            return canonical
    return "generic:" + host


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(Model):
    name: str = Field(default="標準", min_length=1, max_length=80)
    kind: Literal["video", "gallery"]
    container: Literal["original", "mp4", "mkv", "mp3", "audio"] = "original"
    height: int = Field(default=1080, ge=144, le=4320)
    playlist: bool = False
    fragments: int = Field(default=2, ge=1, le=4)
    media_policy: Literal["images", "all"] = "images"
    date_from: date | None = None
    date_to: date | None = None
    timezone: str = "Asia/Tokyo"

    @model_validator(mode="after")
    def valid_dates(self):
        try:
            ZoneInfo(self.timezone)
        except KeyError, ValueError:
            raise ValueError("TIMEZONE_INVALID") from None
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("DATE_RANGE_INVALID")
        if self.kind == "video" and (self.date_from or self.date_to):
            raise ValueError("DATE_UNSUPPORTED")
        return self

    def representation(self) -> str:
        return digest(
            {
                "version": 1,
                "kind": self.kind,
                "container": self.container,
                "height": self.height if self.kind == "video" else None,
                "postprocess": "embed_thumbnail" if self.kind == "video" else None,
            }
        )[:24]


class BatchRequest(Model):
    kind: Literal["video", "gallery"]
    urls: list[str] = Field(min_length=1, max_length=100)
    profile_id: str
    output_root_id: str = "default"
    credential_id: str | None = None

    @model_validator(mode="after")
    def valid_urls(self):
        self.urls = [validate_url(url) for url in self.urls]
        return self


class Settings(Model):
    revision: int = 1
    total_slots: int = Field(default=2, ge=1, le=4)
    video_slots: int = Field(default=1, ge=1, le=2)
    gallery_slots: int = Field(default=1, ge=1, le=2)
    reserve_bytes: int = Field(default=2 * 1024**3, ge=2 * 1024**3)
    time_budget: int = Field(default=21600, ge=60, le=604800)
