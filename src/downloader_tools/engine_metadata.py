"""両エンジンで使う、サイト非依存の小さな完成通知。標準ライブラリのみ使用する。"""

import hashlib
import json
from collections import deque
from itertools import islice
from urllib.parse import urlsplit

AUTHOR_KEYS = (
    "uploader",
    "channel",
    "creator",
    "artist",
    "author",
    "user",
    "owner",
    "username",
    "account",
    "pinner",
    "blog",
    "publisher",
    "uploader_id",
    "channel_id",
)
TITLE_KEYS = (
    "title",
    "fulltitle",
    "subject",
    "caption",
    "description",
    "content",
    "text",
    "filename",
)
ID_KEYS = ("id", "post_id", "tweet_id", "media_id", "status_id", "image_id", "video_id")


def text_value(value: object, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, str):
        return value.strip()[:300]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return next((text for item in value[:16] if (text := text_value(item, depth + 1))), "")
    if isinstance(value, dict):
        for key in (
            "name",
            "display_name",
            "displayName",
            "nick",
            "nickname",
            "username",
            "screen_name",
            "handle",
            "login",
            "title",
            "rendered",
            "text",
            "value",
            "label",
            "profile",
            "attributes",
            "data",
            "node",
            "user",
            "account",
            "id",
        ):
            if text := text_value(value.get(key), depth + 1):
                return text
    return ""


def find_text(metadata: dict, keys: tuple[str, ...]) -> str:
    queue: deque[tuple[object, int]] = deque([(metadata, 0)])
    # 深いAPI応答や巨大なリストも、完成通知のサイズ・処理時間へ伝播させない。
    for _ in range(256):
        if not queue:
            break
        item, depth = queue.popleft()
        if isinstance(item, dict):
            for key in keys:
                if text := text_value(item.get(key)):
                    return text
            if depth < 6:
                queue.extend(
                    (value, depth + 1)
                    for key, value in islice(item.items(), 64)
                    if (not str(key).startswith("_") or key == "_parent")
                    and isinstance(value, (dict, list))
                )
        elif isinstance(item, list) and depth < 6:
            queue.extend(
                (value, depth + 1) for value in item[:16] if isinstance(value, (dict, list))
            )
    return ""


def identity_hash(parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def site_name(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def video_identity(info: dict) -> str:
    category = text_value(info.get("extractor_key") or info.get("extractor"))
    page = info.get("webpage_url") or info.get("original_url") or info.get("url") or ""
    identity = [category, site_name(page), info.get("id")]
    if category.lower() in ("generic", "directlink") or not info.get("id"):
        identity.append(page)
        identity.append(info.get("url"))
    return identity_hash(identity)


def gallery_identity(category: str, page: str, url: str, metadata: dict) -> str:
    # 投稿ID・連番だけでは、ID欠損・階層ごとの番号リセット・同名添付を区別できない。
    return identity_hash([category, site_name(page), find_text(metadata, ID_KEYS), url])


def completion(metadata: dict, category: str, page: str, identity: str) -> dict:
    return {
        "category": text_value(category),
        "site": site_name(page),
        "id": find_text(metadata, ID_KEYS),
        "identity": identity,
        "asset": 0,
        "title": find_text(metadata, TITLE_KEYS),
        "uploader": find_text(metadata, AUTHOR_KEYS),
    }
