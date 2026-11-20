"""Pixiv作品の期間契約。境界はアプリ側、投稿日時は隔離エンジン側で解釈する。"""

import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

GALLERY_VERSION = "1.32.16"
UNKNOWN = "DATE_UNKNOWN"
OUTSIDE = "DATE_OUTSIDE_RANGE"


def supports_url(url):
    parts = urlsplit(url)
    return parts.hostname in {"pixiv.net", "www.pixiv.net"} and bool(
        re.fullmatch(r"/(?:en/)?(?:artworks/[0-9]+|users/[0-9]+/artworks)/?", parts.path)
    )


def bounds(start, end, timezone):
    zone = ZoneInfo(timezone)

    def midnight(day):
        return datetime.combine(day, time.min, zone).timestamp()

    return [
        midnight(start) if start else None,
        midnight(end + timedelta(days=1)) if end and end < date.max else None,
    ]


def skip_reason(category, subcategory, metadata, limits):
    if category != "pixiv" or subcategory not in {"work", "artworks"}:
        return UNKNOWN
    try:
        posted = datetime.fromisoformat(metadata["create_date"])
        if posted.tzinfo is None:
            return UNKNOWN
        instant = posted.timestamp()
    except KeyError, TypeError, ValueError, OverflowError:
        return UNKNOWN
    start, stop = limits
    if (start is not None and instant < start) or (stop is not None and instant >= stop):
        return OUTSIDE
    return None
