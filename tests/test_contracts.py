import functools
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def media_server(tmp_path):
    from downloader_tools.paths import root_path

    ffmpeg = root_path() / "runtime/media/ffmpeg.exe"
    if not ffmpeg.exists():
        pytest.fail("契約検証にはセットアップ済みFFmpegが必要です")
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "sample.png")
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "sample.tiff")
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "sample.bmp")
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp_path / "sample.mp4"),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(tmp_path / "sample.mp4"),
            "-c",
            "copy",
            str(tmp_path / "sample.mov"),
        ],
        check=True,
        timeout=30,
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    Image.new("RGB", (320, 180), "red").save(tmp_path / "cover.jpg")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    # サムネイルを持つ動画ページ。generic抽出器がog:videoとog:imageを取り込む。
    (tmp_path / "page.html").write_text(
        "<html><head><title>Sample Video Title</title>"
        '<meta property="og:title" content="Sample Video Title">'
        f'<meta property="og:video" content="{base}/sample.mp4">'
        '<meta property="og:video:type" content="video/mp4">'
        f'<meta property="og:image" content="{base}/cover.jpg">'
        "</head><body>fixture</body></html>",
        encoding="utf-8",
    )
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "day,posted,expected",
    [
        (
            "2026-03-08",
            [
                "2026-03-09T04:00:00Z",
                "2026-03-08T05:00:00Z",
                "2026-03-08T04:59:59Z",
                None,
                "2026-03-09T03:59:59.999999Z",
            ],
            [2, 5],
        ),
        (
            "2026-11-01",
            [
                "2026-11-02T05:00:00Z",
                "2026-11-01T01:30:00-04:00",
                "2026-11-01T01:30:00-05:00",
                None,
                "2026-11-01T23:59:59-05:00",
            ],
            [2, 3, 5],
        ),
    ],
)
def test_pixiv_date_contract_saves_only_in_range(media_server, tmp_path, day, posted, expected):
    """作品一覧の取得元だけをfixtureへ置換し、固定版extractorと実転送を通す。"""
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.paths import root_path

    work = tmp_path / "work"
    source = {
        "kind": "gallery",
        "url": "https://www.pixiv.net/users/123/artworks",
        "profile": Profile(
            kind="gallery", date_from=day, date_to=day, timezone="America/New_York"
        ).model_dump_json(),
    }
    manifest = json.loads(
        next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
    )
    command = build_command(source, manifest, work, None)
    works = []
    for i, stamp in enumerate(posted, 1):
        # 除外予定のURLは存在しない。誤って転送すると実エンジンが失敗する。
        url = f"{media_server}/{'sample' if i in expected else 'missing'}.png?i={i}"
        works.append(
            dict(
                id=i,
                user={"id": 1},
                tags=[],
                type="illust",
                x_restrict=0,
                create_date=stamp,
                meta_single_page={"original_image_url": url},
                image_urls={},
                meta_pages=[],
            )
        )
    i = command.index(str(root_path() / "src/downloader_tools/gallery_engine.py"))
    for work_item in works:
        if work_item["create_date"] is None:
            del work_item["create_date"]
    entry = command[i]
    code = (
        "import runpy,sys,json; from gallery_dl.extractor.pixiv import PixivArtworksExtractor; "
        f"PixivArtworksExtractor.works=lambda self: json.loads({json.dumps(works)!r}); "
        f"sys.argv[0]={entry!r}; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    run = subprocess.run(
        command[:i] + ["-c", code] + command[i + 1 :],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    events = [parse_line(line) for line in run.stdout.splitlines() if line.startswith("DT_ITEM\t")]
    assert [int(e["id"]) for e in events if e["path"]] == expected
    assert {Path(e["path"]) for e in events if e["path"]} == set((work / "payload").glob("*"))
    for e in events:
        if e["path"]:
            assert Path(e["path"]).read_bytes() == (tmp_path / "sample.png").read_bytes()
    assert [e.get("policy_skip") for e in events] == [
        None if i in expected else "DATE_UNKNOWN" if stamp is None else "DATE_OUTSIDE_RANGE"
        for i, stamp in enumerate(posted, 1)
    ]


@pytest.mark.parametrize(
    "kind,extension",
    [
        ("video", "mp4"),
        ("video", "mov"),
        ("gallery", "png"),
        ("gallery", "bmp"),
    ],
)
def test_real_engine_download_and_structured_completion(media_server, tmp_path, kind, extension):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.paths import root_path

    manifest_file = next((root_path() / "runtime/toolsets").glob("*/manifest.json"))
    manifest = json.loads(manifest_file.read_text())
    url = media_server + "/sample." + extension
    source = {
        "id": "contract",
        "kind": kind,
        "url": url,
        "profile": Profile(kind=kind).model_dump_json(),
    }
    command = build_command(source, manifest, tmp_path / "work", None)
    output = subprocess.run(command, capture_output=True, timeout=45)
    assert output.returncode == 0, output.stderr.decode(errors="replace")
    events = [
        parse_line(line)
        for line in output.stdout.decode().splitlines()
        if line.startswith("DT_ITEM")
    ]
    assert len(events) == 1, output.stdout.decode(errors="replace")
    assert Path(events[0]["path"]).stat().st_size > 0


@pytest.mark.parametrize("title", ["Sample Video Title", "動画🎬「引用」 / :?"])
def test_video_engine_reports_title_and_rerun_keeps_the_candidate(media_server, tmp_path, title):
    import hashlib
    from html import escape

    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.paths import root_path

    manifest = json.loads(
        next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
    )
    source = {
        "id": "resume",
        "kind": "video",
        "url": media_server + "/page.html",
        "profile": Profile(kind="video").model_dump_json(),
    }
    work = tmp_path / "work"
    page = tmp_path / "page.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace("Sample Video Title", escape(title)),
        encoding="utf-8",
    )
    first = subprocess.run(
        build_command(source, manifest, work, None), capture_output=True, timeout=60
    )
    assert first.returncode == 0, first.stderr.decode(errors="replace")
    events = [
        parse_line(line)
        for line in first.stdout.decode().splitlines()
        if line.startswith("DT_ITEM")
    ]
    assert len(events) == 1, first.stdout.decode(errors="replace")
    assert events[0]["title"] == title
    media = Path(events[0]["path"])
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    assert not list(media.parent.glob("*.jpg")) and not list(media.parent.glob("*.webp"))
    ffprobe = root_path() / manifest["ffprobe"]
    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream_disposition=attached_pic",
            "-of",
            "json",
            str(media),
        ],
        capture_output=True,
        timeout=30,
    )
    assert [s["disposition"]["attached_pic"] for s in json.loads(probe.stdout)["streams"]] == [
        0,
        1,
    ]

    # 公開済み実体の埋め込みをスキップし、新規項目は再開時にも後処理する。
    (tmp_path / "published.mp4").hardlink_to(media)
    command = build_command(source, manifest, work, None)
    assert "--embed-thumbnail" in command
    second = subprocess.run(command, capture_output=True, timeout=60)
    assert second.returncode == 0, second.stderr.decode(errors="replace")
    assert hashlib.sha256(media.read_bytes()).hexdigest() == digest
    assert not list(media.parent.glob("*.png"))
    # 別IDの項目を同じ作業領域へ追加し、再開後も埋め込まれることを確かめる。
    source["url"] = media_server + "/page-new.html"
    page = tmp_path / "page.html"
    import shutil

    shutil.copyfile(tmp_path / "sample.mp4", tmp_path / "sample-new.mp4")
    (tmp_path / "page-new.html").write_text(
        page.read_text(encoding="utf-8").replace("sample.mp4", "sample-new.mp4"), encoding="utf-8"
    )
    third = subprocess.run(
        build_command(source, manifest, work, None), capture_output=True, timeout=60
    )
    assert third.returncode == 0, third.stderr.decode(errors="replace")
    new_events = [
        parse_line(line)
        for line in third.stdout.decode().splitlines()
        if line.startswith("DT_ITEM")
    ]
    new_media = Path(new_events[0]["path"])
    assert new_media != media
    new_probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "stream_disposition=attached_pic",
            "-of",
            "json",
            str(new_media),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert any(s["disposition"]["attached_pic"] for s in json.loads(new_probe.stdout)["streams"])


def test_mp3_thumbnail_is_embedded(media_server, tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.paths import root_path

    manifest = json.loads(
        next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
    )
    source = {
        "id": "mp3",
        "kind": "video",
        "url": media_server + "/page.html",
        "profile": Profile(kind="video", container="mp3").model_dump_json(),
    }
    result = subprocess.run(
        build_command(source, manifest, tmp_path / "work", None), capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    event = next(
        parse_line(line)
        for line in result.stdout.decode().splitlines()
        if line.startswith("DT_ITEM")
    )
    path = Path(event["path"])
    assert path.suffix == ".mp3"
    probe = subprocess.run(
        [
            str(root_path() / manifest["ffprobe"]),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert any(s["codec_name"] == "mp3" for s in streams)
    assert any(s["disposition"]["attached_pic"] for s in streams)


@pytest.mark.parametrize("container", ["original", "mp4"])
def test_single_webm_is_remuxed_and_embedded(media_server, tmp_path, container):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.paths import root_path

    manifest = json.loads(
        next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
    )
    subprocess.run(
        [
            str(root_path() / manifest["ffmpeg"]),
            "-v",
            "error",
            "-i",
            str(tmp_path / "sample.mp4"),
            "-c:v",
            "libvpx-vp9",
            "-c:a",
            "libopus",
            str(tmp_path / "sample.webm"),
        ],
        timeout=30,
        check=True,
    )
    (tmp_path / "webm.html").write_text(
        (tmp_path / "page.html")
        .read_text()
        .replace("sample.mp4", "sample.webm")
        .replace("video/mp4", "video/webm"),
        encoding="utf-8",
    )
    source = {
        "id": "webm",
        "kind": "video",
        "url": media_server + "/webm.html",
        "profile": Profile(kind="video", container=container).model_dump_json(),
    }
    result = subprocess.run(
        build_command(source, manifest, tmp_path / "work", None), capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    event = next(
        parse_line(line)
        for line in result.stdout.decode().splitlines()
        if line.startswith("DT_ITEM")
    )
    path = Path(event["path"])
    assert path.suffix == (".mkv" if container == "original" else ".mp4")
    probe = subprocess.run(
        [
            str(root_path() / manifest["ffprobe"]),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert any(s["codec_name"] == ("vp9" if container == "original" else "h264") for s in streams)
    assert any(
        s.get("tags", {}).get("filename") == "cover.png"
        or s.get("disposition", {}).get("attached_pic")
        for s in streams
    )
    import hashlib

    before = hashlib.sha256(path.read_bytes()).digest()
    (tmp_path / "published.mkv").hardlink_to(path)
    resumed = subprocess.run(
        build_command(source, manifest, tmp_path / "work", None), capture_output=True, timeout=60
    )
    assert resumed.returncode == 0, resumed.stderr.decode(errors="replace")
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("media_policy,expected", [("images", 6), ("all", 14)])
async def test_nested_gallery_tree_with_repeated_names_and_mixed_media(
    media_server, tmp_path, monkeypatch, capsys, media_policy, expected
):
    import asyncio
    import hashlib

    from gallery_dl import config
    from gallery_dl.extractor.common import Extractor, Message

    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command, parse_line
    from downloader_tools.events import Events
    from downloader_tools.gallery_engine import StructuredDownloadJob
    from downloader_tools.paths import root_path
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    Image.new("RGB", (16, 16), "green").save(tmp_path / "sample.tiff")
    import shutil
    import zipfile

    shutil.copyfile(tmp_path / "sample.mp4", tmp_path / "second.mp4")
    with zipfile.ZipFile(tmp_path / "sample.zip", "w") as archive:
        archive.writestr("image.png", (tmp_path / "sample.png").read_bytes())
    (tmp_path / "multi.html").write_text(
        '<html><title>動画一覧</title><video src="/sample.mp4" controls></video>'
        '<video src="/second.mp4" controls></video></html>',
        encoding="utf-8",
    )

    class Leaf(Extractor):
        category = "unknown-site"
        subcategory = "post"
        pattern = r"https?://[^/]+/child/(\d+)"
        directory_fmt = ("{nonexistent[unsafe]}", "a" * 500)
        filename_fmt = "{missing}.{extension}"

        def items(self):
            yield Message.Directory, "", {}
            for index, extension in enumerate(("png", "png", "tiff", "mp4", "ytdl", "zip")):
                url = (
                    "ytdl:" + media_server + "/multi.html"
                    if extension == "ytdl"
                    else media_server + f"/sample.{extension}"
                )
                yield (
                    Message.Url,
                    url + f"?post={self.groups[0]}&asset={index}",
                    {
                        "id": "same-id",
                        "num": 1,
                        "filename": "same-name",
                        "extension": extension,
                        "data": {
                            "node": {
                                "caption": "作品" * 100_000,
                                "owner": {"profile": {"name": "作者"}},
                            }
                        },
                    },
                )

    class Album(Extractor):
        category = "unknown-site"
        subcategory = "album"
        pattern = r"https?://[^/]+/album"

        def items(self):
            yield Message.Directory, "", {"title": "アルバム"}
            for number in (1, 2):
                yield Message.Queue, media_server + f"/child/{number}", {"_extractor": Leaf}

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        manifest = json.loads(
            next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
        )
        await store.write(
            lambda c: c.execute(
                "INSERT INTO toolsets VALUES(?,?,1)", (manifest["id"], json.dumps(manifest))
            )
        )
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/album"],
                "profile_id": "gallery-default",
            },
            "tree",
        )
        source = await store.claim(batch["sources"][0]["id"])
        source["profile"] = Profile(kind="gallery", media_policy=media_policy).model_dump_json()
        source["url"] = media_server + "/album"
        work = output / ".downloader/work" / source["id"]
        build_command(source, manifest, work, None)
        for key, value in json.loads((work / "gallery-config.json").read_text()).items():
            monkeypatch.setitem(config._config, key, value)
        scheduler = Scheduler(store, tmp_path, Events())
        initial = {}
        for attempt in range(2):
            assert (
                await asyncio.to_thread(StructuredDownloadJob(Album.from_url(source["url"])).run)
                == 0
            )
            events = [
                parse_line(line)
                for line in capsys.readouterr().out.splitlines()
                if line.startswith("DT_ITEM")
            ]
            skipped = [event for event in events if event.get("policy_skip")]
            assert len(skipped) == (6 if media_policy == "images" else 0)
            for event in skipped:
                assert event["path"] == ""
                await scheduler.publish_event(source, event)
            events = [event for event in events if not event.get("policy_skip")]
            assert len(events) == expected
            assert len({event["identity"] for event in events}) == expected
            for event in events:
                path = Path(event["path"])
                assert path.parent == work / "payload"
                assert len(path.stem) == 64
                assert len(json.dumps(event)) < 4096
                fingerprint = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).digest()
                if attempt:
                    assert fingerprint == initial[path]
                initial[path] = fingerprint
                await scheduler.publish_event(source, event)
        artifacts = await store.artifacts(source["id"])
        assert len(artifacts) == expected
        if media_policy == "images":
            assert (await store.source(source["id"]))["skipped_count"] == 6
            assert not list((work / "payload").glob("*.zip"))
            assert not list((work / "payload").glob("*.mp4"))
        for artifact in artifacts:
            relative = Path(artifact["relative_path"])
            assert relative.parts[:2] == ("unknown-site", "作者")
            assert len(relative.parts) == 3
            assert (output / relative).is_file()
    finally:
        await store.close()


@pytest.mark.parametrize("content", ["video", "document"])
async def test_images_only_skips_nonimage_identified_after_download(
    media_server, tmp_path, content
):
    import shutil

    from downloader_tools.paths import root_path
    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "downloads"
        root.mkdir()
        await store.register_root("default", root)
        manifest = json.loads(
            next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
        )
        await store.write(
            lambda c: c.execute(
                "INSERT INTO toolsets VALUES(?,?,1)", (manifest["id"], json.dumps(manifest))
            )
        )
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/album"],
                "profile_id": "gallery-default",
            },
            "opaque",
        )
        source = await store.claim(batch["sources"][0]["id"])
        payload = root / ".downloader/work" / source["id"] / "payload"
        payload.mkdir(parents=True)
        candidate = payload / "opaque.bin"
        if content == "video":
            shutil.copyfile(tmp_path / "sample.mp4", candidate)
        else:
            candidate.write_bytes(b"a non-image attachment without a recognizable extension")
        publisher = Publisher(store)
        assert (
            await publisher.publish(
                source,
                {
                    "path": str(candidate),
                    "media_key": "opaque",
                    "receipt_id": 1,
                },
            )
            == "MEDIA_POLICY_IMAGES_ONLY"
        )
        assert (await store.source(source["id"]))["skipped_count"] == 1
        assert not await store.artifacts(source["id"])
    finally:
        await store.close()


def video_with_embedded_cover(folder: Path) -> Path:
    from downloader_tools.paths import root_path

    ffmpeg = root_path() / "runtime/media/ffmpeg.exe"
    if not ffmpeg.exists():
        pytest.fail("契約検証にはセットアップ済みFFmpegが必要です")
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(folder / "plain.mp4"),
        ],
        check=True,
        timeout=30,
    )
    Image.new("RGB", (320, 180), "red").save(folder / "cover.png")
    # 埋め込みサムネイルは映像ストリームとして現れ、解像度が上限を超える。品質判定から除外する。
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-i",
            str(folder / "plain.mp4"),
            "-i",
            str(folder / "cover.png"),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c",
            "copy",
            "-disposition:v:1",
            "attached_pic",
            str(folder / "candidate.mp4"),
        ],
        check=True,
        timeout=30,
    )
    return folder / "candidate.mp4"


@pytest.mark.asyncio
async def test_validation_ignores_embedded_thumbnail_stream(tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.paths import root_path
    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    candidate = video_with_embedded_cover(tmp_path)
    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        manifest = json.loads(
            next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
        )
        await store.write(
            lambda c: c.execute(
                "INSERT INTO toolsets VALUES(?,?,1)", (manifest["id"], json.dumps(manifest))
            )
        )
        batch = await store.create_batch(
            {
                "kind": "video",
                "urls": ["https://example.com/sample"],
                "profile_id": "video-default",
            },
            "thumbnail",
        )
        source = await store.claim(batch["sources"][0]["id"])
        source["profile"] = Profile(kind="video", container="mp4", height=144).model_dump_json()
        await Publisher(store).validate(candidate, source)
    finally:
        await store.close()


def test_receipt_is_written_by_real_sourcehost_and_job_kills_descendants(tmp_path):
    import psutil

    from downloader_tools.paths import atomic_json

    config = tmp_path / "run.json"
    atomic_json(
        config,
        {
            "source_id": "s",
            "attempt_id": "a",
            "run_token": "token",
            "owner_path": str(tmp_path / "owner.json"),
            "receipts": str(tmp_path / "receipts"),
            "command": [
                sys.executable,
                "-c",
                "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('started',flush=True); time.sleep(60)",
            ],
            "log_path": str(tmp_path / "log.txt"),
        },
    )
    host = subprocess.Popen(
        [sys.executable, "-m", "downloader_tools.source_host", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert host.stdout and host.stdin
    ready = json.loads(host.stdout.readline())
    host.stdin.write(b'{"action":"start"}\n')
    host.stdin.flush()
    assert json.loads(host.stdout.readline())["type"] == "started"
    import time

    time.sleep(0.3)
    owner = psutil.Process(ready["owner"]["pid"])
    descendants = [(p.pid, p.create_time()) for p in owner.children(recursive=True)]
    assert descendants
    owner.kill()
    host.wait(timeout=10)
    for pid, created in descendants:
        try:
            process = psutil.Process(pid)
            process.wait(10)
            assert not process.is_running() or process.create_time() != created
        except psutil.NoSuchProcess:
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,extension",
    [
        ("video", "mp4"),
        ("video", "mov"),
        ("gallery", "png"),
        ("gallery", "bmp"),
    ],
)
async def test_engine_to_host_to_publisher_pipeline(media_server, tmp_path, kind, extension):
    from downloader_tools.events import Events
    from downloader_tools.paths import root_path
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    root = tmp_path / "app"
    store = Store(root / "data/app.sqlite3")
    await store.start()
    try:
        output = root / "downloads"
        output.mkdir(parents=True)
        await store.register_root("default", output)
        manifest = json.loads(
            next((root_path() / "runtime/toolsets").glob("*/manifest.json")).read_text()
        )
        await store.write(
            lambda c: c.execute(
                "INSERT INTO toolsets VALUES(?,?,1)", (manifest["id"], json.dumps(manifest))
            )
        )
        batch = await store.create_batch(
            {"kind": kind, "urls": ["https://example.com/sample"], "profile_id": kind + "-default"},
            "pipeline",
        )
        source = await store.claim(batch["sources"][0]["id"])
        # ローカルfixtureはAPI/起動前の公開宛先検査と分離してエンジン契約だけへ渡す。
        source["url"] = media_server + "/sample." + extension
        source["manifest"] = manifest
        scheduler = Scheduler(store, root, Events())
        scheduler.active[source["id"]] = source
        await scheduler.run(source, output)
        finished = await store.source(source["id"])
        assert finished["state"] == "succeeded", (finished["state"], finished["reason"])
        artifacts = await store.artifacts(source["id"])
        assert len(artifacts) == 1
        assert (output / artifacts[0]["relative_path"]).is_file()
        assert Path(artifacts[0]["relative_path"]).parts == (
            "127.0.0.1",
            "投稿者不明",
            "sample." + extension,
        )
    finally:
        await store.close()
