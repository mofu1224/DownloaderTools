import asyncio
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from .domain import Profile, digest
from .engine_metadata import AUTHOR_KEYS, TITLE_KEYS, find_text, text_value
from .gallery_dates import OUTSIDE, UNKNOWN
from .media_policy import IMAGES_ONLY, NON_IMAGE_EXTENSIONS, MediaPolicySkip
from .paths import contained, root_path
from .store import Store, uid

TITLE_LIMIT = 80
# 保存名に使う余地。保存先の絶対パス長をWindowsの上限内に収める。
PATH_LIMIT = 240
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    "CONIN$",
    "CONOUT$",
    "COM¹",
    "COM²",
    "COM³",
    "LPT¹",
    "LPT²",
    "LPT³",
}


def safe_title(value: object) -> str:
    """サイト由来のタイトルを、保存先で安全に使える短縮名へ変換する。"""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFC", value)
    text = re.sub(r"""[\x00-\x1f\x7f\ud800-\udfff<>:"/\\|?*]""", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")[:TITLE_LIMIT].strip(" .")
    while len(text.encode("utf-8")) > 200:
        text = text[:-1]
    if not text or text in (".", "..") or text.split(".", 1)[0].upper() in RESERVED_NAMES:
        return ""
    return text


def title_filename(title: str, stem: str, suffix: str, room: int) -> str:
    """通常はタイトルだけを使い、衝突した場合だけ識別子を残す。"""
    keeper = f"_{stem}{suffix}" if stem else suffix
    short = title
    while short and path_length(short + keeper) > room:
        short = short[:-1]
    short = safe_title(short)
    if not short:
        short = "untitled"
    if path_length(short + keeper) > room:
        raise ValueError("OUTPUT_PATH_TOO_LONG")
    return short + keeper


def path_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def metadata_name(value: object) -> str:
    return safe_title(text_value(value))


def output_path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\\", "/")).casefold()


def output_names(source: dict, event: dict, stem: str) -> tuple[str, str, str]:
    provider = str(source.get("provider") or "")
    category = metadata_name(event.get("category"))
    host = metadata_name(event.get("site")) or (
        urlsplit(source.get("url", "")).hostname or ""
    ).removeprefix("www.")
    platform = provider if provider and not provider.startswith("generic:") else category
    if not platform or platform.lower() in ("generic", "directlink"):
        platform = host or provider.removeprefix("generic:")
    platform = {
        "youtube": "YouTube",
        "twitter": "X",
        "instagram": "Instagram",
        "reddit": "Reddit",
    }.get(platform.lower(), platform)
    author = safe_title(find_text(event, AUTHOR_KEYS)) or "投稿者不明"
    identity = metadata_name(event.get("id")) or stem[:12]
    title = safe_title(find_text(event, TITLE_KEYS)) or identity
    return safe_title(platform) or "サイト不明", author, title


class Publisher:
    def __init__(self, store: Store):
        self.store = store
        self.lock = asyncio.Lock()

    async def root(self, root_id):
        rows = await self.store.read("SELECT * FROM output_roots WHERE id=?", (root_id,))
        if not rows:
            raise ValueError("OUTPUT_NOT_FOUND")
        row = rows[0]
        path = Path(row["path"])
        return path if path.is_absolute() else root_path() / path

    async def prepare(self, source, event):
        root = await self.root(source["output_root_id"])
        work = root / ".downloader/work" / source["id"] / "payload"
        path = contained(work, Path(event["path"]))
        receipt_key = digest(
            [event.get("receipt_id"), event["media_key"], str(path.relative_to(root))]
        )
        previous = await self.store.read(
            "SELECT p.* FROM publication_receipts r JOIN publish_intents p "
            "ON p.id=r.intent_id WHERE r.attempt_id=? AND r.receipt_key=?",
            (source["attempt_id"], receipt_key),
        )
        if previous:
            return previous[0]
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if not size or not re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) or not path.is_file():
            raise ValueError("CANDIDATE_INVALID")
        profile = Profile.model_validate_json(source["profile"])
        if (
            profile.kind == "gallery"
            and profile.media_policy == "images"
            and suffix[1:] in NON_IMAGE_EXTENSIONS
        ):
            raise MediaPolicySkip()
        if suffix in {".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".js", ".lnk", ".msi"}:
            raise ValueError("MEDIA_EXTENSION_UNSAFE")
        await self.validate(path, source)
        representation = Profile.model_validate_json(source["profile"]).representation()
        key = event["media_key"]
        if not isinstance(key, str) or not key or len(key) > 2048:
            raise ValueError("MEDIA_KEY_INVALID")
        engine = "yt-dlp" if source["kind"] == "video" else "gallery-dl"
        stem = digest([engine, key, representation])[:12]
        platform, author, title = output_names(source, event, stem)
        # 深い保存先でもファイル名の余地を確保する。
        folder_room = PATH_LIMIT - path_length(str(root)) - 1 - 32
        if folder_room < 5:
            raise ValueError("OUTPUT_PATH_TOO_LONG")
        limit = max(1, (folder_room - 2) // 2)
        platform = title_filename(platform, "", "", limit)
        author = title_filename(author, "", "", limit)
        directory = Path(platform) / author
        published = await self.store.read(
            "SELECT relative_path FROM artifacts WHERE output_root_id=? AND engine=? "
            "AND media_key=? AND representation_key=?",
            (source["output_root_id"], engine, key, representation),
        )
        if published:
            # 一度公開したパスは、タイトルが変わっても再取得時に動かさない。
            final = Path(published[0]["relative_path"])
        else:
            room = PATH_LIMIT - path_length(str(root / directory)) - 1
            final = directory / title_filename(title, "", path.suffix.lower(), room)
        intent = dict(
            id=uid(),
            source_id=source["id"],
            attempt_id=source["attempt_id"],
            output_root_id=source["output_root_id"],
            engine=engine,
            media_key=key,
            representation_key=representation,
            candidate_path=str(path.relative_to(root)),
            final_path=str(final),
            size=size,
            state="prepared",
            created_at=time.time(),
        )

        def prepare_intent(c):
            def compare(left, right):
                left, right = output_path_key(left), output_path_key(right)
                return (left > right) - (left < right)

            c.create_collation("OUTPUT_PATH", compare)
            reserved = c.execute(
                "SELECT final_path FROM publish_intents WHERE output_root_id=? AND engine=? "
                "AND media_key=? AND representation_key=? LIMIT 1",
                (source["output_root_id"], engine, key, representation),
            ).fetchone()
            if reserved:
                intent["final_path"] = reserved["final_path"]
            elif not published:
                number = 0
                while (root / str(intent["final_path"])).exists() or c.execute(
                    "SELECT 1 FROM publish_intents WHERE output_root_id=? "
                    "AND final_path=? COLLATE OUTPUT_PATH UNION ALL "
                    "SELECT 1 FROM artifacts WHERE output_root_id=? "
                    "AND relative_path=? COLLATE OUTPUT_PATH LIMIT 1",
                    (
                        source["output_root_id"],
                        intent["final_path"],
                        source["output_root_id"],
                        intent["final_path"],
                    ),
                ).fetchone():
                    number += 1
                    tag = stem if number == 1 else f"{stem}_{number}"
                    intent["final_path"] = str(
                        directory / title_filename(title, tag, path.suffix.lower(), room)
                    )
            c.execute(
                "INSERT INTO publish_intents VALUES(:id,:source_id,:attempt_id,:output_root_id,:engine,"
                ":media_key,:representation_key,:candidate_path,:final_path,:size,:state,:created_at)",
                intent,
            )
            c.execute(
                "INSERT INTO publication_receipts VALUES(?,?,?)",
                (source["attempt_id"], receipt_key, intent["id"]),
            )

        await self.store.write(prepare_intent)
        return intent

    async def validate(self, path, source):
        # 拡張子の固定リストで拒否せず、Pillowとffprobeで内容を判定する。
        profile = Profile.model_validate_json(source["profile"])
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).with_name("image_probe.py")),
            str(path),
            *(
                ["--images-only"]
                if profile.kind == "gallery" and profile.media_policy == "images"
                else []
            ),
            *(
                ["--allow-archives"]
                if profile.kind == "gallery" and profile.media_policy == "all"
                else []
            ),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                image_result = await process.wait()
                if image_result == 1:
                    raise ValueError("IMAGE_INVALID")
                if image_result == 3:
                    raise ValueError("ARCHIVE_INVALID")
                if image_result == 4:
                    raise MediaPolicySkip()
                if image_result == 0:
                    return
                if image_result != 2:
                    raise ValueError("IMAGE_PROBE_FAILED")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await self.validate_streams(path, source)

    async def validate_streams(self, path, source):
        tools = await self.store.read(
            "SELECT manifest FROM toolsets WHERE id=?", (source["toolset_id"],)
        )
        if not tools:
            raise ValueError("FFPROBE_UNAVAILABLE")
        manifest = json.loads(tools[0]["manifest"])
        probe = root_path() / manifest["ffprobe"]
        process = await asyncio.create_subprocess_exec(
            str(probe),
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "stream=codec_type,codec_name,height:stream_disposition:format=format_name:format_tags=major_brand",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                assert process.stdout
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    output.extend(chunk)
                    if len(output) > 1024 * 1024:
                        raise ValueError("PROBE_OUTPUT_LIMIT")
                code = await process.wait()
            metadata = json.loads(output)
            profile = Profile.model_validate_json(source["profile"])
            if code or not any(
                stream.get("codec_type") in ("video", "audio")
                for stream in metadata.get("streams", [])
            ):
                if profile.kind == "gallery" and profile.media_policy == "images":
                    raise MediaPolicySkip()
                raise ValueError("MEDIA_INVALID")
            if profile.kind == "gallery" and profile.media_policy == "images":
                format_name = metadata.get("format", {}).get("format_name", "")
                brand = metadata.get("format", {}).get("tags", {}).get("major_brand", "").strip()
                if not (
                    format_name.endswith("_pipe")
                    or format_name == "image2"
                    or brand
                    in {"heic", "heix", "hevc", "hevx", "mif1", "msf1", "avif", "avis", "heif"}
                ):
                    raise MediaPolicySkip()
            for stream in metadata["streams"]:
                if stream.get("disposition", {}).get("attached_pic"):
                    continue
                if profile.kind == "video" and stream.get("height", 0) > profile.height:
                    raise ValueError("RESOLUTION_LIMIT_EXCEEDED")
                if profile.container == "mp4" and stream.get("codec_type") in ("video", "audio"):
                    expected = "h264" if stream["codec_type"] == "video" else "aac"
                    if stream.get("codec_name") != expected:
                        raise ValueError("MP4_CODEC_MISMATCH")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def commit(self, intent):
        root = await self.root(intent["output_root_id"])
        candidate = contained(
            root / ".downloader/work" / intent["source_id"] / "payload",
            root / intent["candidate_path"],
        )
        final = contained(root, Path(intent["final_path"]))

        def link():
            final.parent.mkdir(parents=True, exist_ok=True)
            if candidate.exists() and candidate.stat().st_size != intent["size"]:
                raise ValueError("CANDIDATE_CHANGED")
            if final.exists():
                if final.stat().st_size != intent["size"]:
                    raise ValueError("OUTPUT_CONFLICT")
                if candidate.exists() and not os.path.samefile(candidate, final):

                    def sha(path):
                        with path.open("rb") as stream:
                            return hashlib.file_digest(stream, "sha256").digest()

                    if sha(candidate) != sha(final):
                        raise ValueError("OUTPUT_CONFLICT")
                elif not candidate.exists() and intent["state"] != "committed":
                    raise ValueError("OUTPUT_IDENTITY_UNPROVEN")
            elif candidate.exists():
                with candidate.open("r+b") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(candidate, final)
            else:
                raise ValueError("CANDIDATE_MISSING")

        await asyncio.to_thread(link)

        def record(conn):
            previous = conn.execute(
                "SELECT state FROM publish_intents WHERE id=?", (intent["id"],)
            ).fetchone()
            if previous[0] == "committed":
                return
            existing = conn.execute(
                "SELECT id,relative_path FROM artifacts WHERE output_root_id=? AND engine=? "
                "AND media_key=? AND representation_key=?",
                (
                    intent["output_root_id"],
                    intent["engine"],
                    intent["media_key"],
                    intent["representation_key"],
                ),
            ).fetchone()
            if existing and existing["relative_path"] != intent["final_path"]:
                raise ValueError("ARTIFACT_REPRESENTATION_CONFLICT")
            conn.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,1,?) "
                "ON CONFLICT(output_root_id,engine,media_key,representation_key) DO UPDATE SET "
                "valid=1,size=excluded.size,checked_at=excluded.checked_at",
                (
                    uid(),
                    intent["source_id"],
                    intent["attempt_id"],
                    intent["output_root_id"],
                    intent["engine"],
                    intent["media_key"],
                    intent["representation_key"],
                    intent["final_path"],
                    intent["size"],
                    time.time(),
                ),
            )
            conn.execute("UPDATE publish_intents SET state='committed' WHERE id=?", (intent["id"],))
            column = "skipped_count" if existing else "completed_count"
            conn.execute(
                f"UPDATE sources SET {column}={column}+1,revision=revision+1 WHERE id=?",
                (intent["source_id"],),
            )

        await self.store.write(record)
        # Sourceが終端になるまでは作業側リンクを保持し、再開時の再転送を防ぐ。
        # 公開後の変更は禁止し、エンジン側の非上書き指定と契約試験で固定する。

    async def publish(self, source, event):
        async with self.lock:
            profile = Profile.model_validate_json(source["profile"])
            images_only = profile.kind == "gallery" and profile.media_policy == "images"
            if event.get("policy_skip"):
                reason = event["policy_skip"]
                if not (
                    (images_only and reason == IMAGES_ONLY)
                    or ((profile.date_from or profile.date_to) and reason in (OUTSIDE, UNKNOWN))
                ):
                    raise ValueError("ENGINE_PROTOCOL_INVALID")
                await self.record_policy_skip(source, event, reason)
                return reason
            try:
                intent = await self.prepare(source, event)
            except MediaPolicySkip:
                if not images_only:
                    raise
                await self.record_policy_skip(source, event)
                return IMAGES_ONLY
            await self.commit(intent)

    async def record_policy_skip(self, source, event, reason=IMAGES_ONLY):
        key = event["media_key"]
        if not isinstance(key, str) or not key or len(key) > 2048:
            raise ValueError("MEDIA_KEY_INVALID")

        def record(conn):
            inserted = conn.execute(
                "INSERT OR IGNORE INTO policy_skips VALUES(?,?,?,?)",
                (source["id"], key, reason, time.time()),
            ).rowcount
            if inserted:
                conn.execute(
                    "UPDATE sources SET skipped_count=skipped_count+1,revision=revision+1 WHERE id=?",
                    (source["id"],),
                )

        await self.store.write(record)

    async def check_catalog(self, source_id):
        cursor = 0
        while True:
            rows = await self.store.read(
                "SELECT rowid AS cursor,* FROM artifacts WHERE source_id=? AND rowid>? "
                "ORDER BY rowid LIMIT 200",
                (source_id, cursor),
            )
            if not rows:
                break
            invalid = []
            for row in rows:
                root = await self.root(row["output_root_id"])

                def present(item=row, folder=root):
                    path = contained(folder, Path(item["relative_path"]))
                    return path.is_file() and path.stat().st_size == item["size"]

                if not await asyncio.to_thread(present):
                    invalid.append(row["id"])
            if invalid:
                await self.store.write(
                    lambda c, ids=invalid: c.executemany(
                        "UPDATE artifacts SET valid=0,checked_at=? WHERE id=?",
                        [(time.time(), identity) for identity in ids],
                    )
                )
            cursor = rows[-1]["cursor"]

    async def cleanup_links(self, source_id):
        cursor = 0
        while True:
            rows = await self.store.read(
                "SELECT rowid AS cursor,* FROM publish_intents WHERE source_id=? "
                "AND state='committed' AND rowid>? ORDER BY rowid LIMIT 200",
                (source_id, cursor),
            )
            if not rows:
                return
            for intent in rows:
                root = await self.root(intent["output_root_id"])

                def unlink(item=intent, folder=root):
                    candidate = contained(
                        folder / ".downloader/work" / item["source_id"] / "payload",
                        folder / item["candidate_path"],
                    )
                    final = contained(folder, Path(item["final_path"]))
                    if candidate.exists() and final.exists() and os.path.samefile(candidate, final):
                        candidate.unlink()

                await asyncio.to_thread(unlink)
            cursor = rows[-1]["cursor"]

    async def recover(self, root: Path):
        roots = await self.store.roots()
        resolved = await asyncio.to_thread(root.resolve)
        ids = [
            r["id"]
            for r in roots
            if (
                Path(r["path"]) if Path(r["path"]).is_absolute() else root_path() / r["path"]
            ).resolve()
            == resolved
        ]
        async with self.lock:
            for root_id in ids:
                intents = await self.store.read(
                    "SELECT * FROM publish_intents WHERE output_root_id=? AND state='prepared'",
                    (root_id,),
                )
                for intent in intents:
                    await self.commit(intent)
