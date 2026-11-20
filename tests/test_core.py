import asyncio

import pytest


def test_public_url_validation_and_representation():
    from downloader_tools.domain import Profile, validate_url

    assert validate_url("https://youtu.be/abc?signature=keep").endswith("signature=keep")
    for url in ("file:///secret", "http://127.0.0.1/a", "https://user:pass@site.test/a"):
        with pytest.raises(ValueError):
            validate_url(url)
    assert (
        Profile(kind="video", container="mp4").representation()
        != Profile(kind="video", container="mkv").representation()
    )


def test_title_is_sanitized_into_a_safe_filename():
    from downloader_tools.publisher import safe_title, title_filename

    assert safe_title('  a/b:c*d?e"f<g>h|i  ') == "a b c d e f g h i"
    assert safe_title("../../etc/passwd") == "etc passwd"
    assert safe_title("CON") == "" and safe_title("aux.txt") == ""
    assert safe_title(None) == "" and safe_title(123) == ""
    assert len(safe_title("あ" * 200).encode("utf-8")) <= 200
    assert len(safe_title("😀" * 200).encode("utf-8")) <= 200
    stem = "abcdef0123456789"
    assert title_filename("作品タイトル", "", ".mp4", 100) == "作品タイトル.mp4"
    assert title_filename("作品タイトル", stem, ".mp4", 100) == f"作品タイトル_{stem}.mp4"
    keeper = f"_{stem}.mp4"
    assert title_filename("作品タイトル", stem, ".mp4", len(keeper) + 2) == "作品" + keeper


@pytest.mark.parametrize(
    "source,event,expected",
    [
        (
            {"provider": "youtube"},
            {"uploader": "作者", "title": "題名"},
            ("YouTube", "作者", "題名"),
        ),
        (
            {"provider": "twitter"},
            {"user": {"nick": "表示名"}, "title": "本文"},
            ("X", "表示名", "本文"),
        ),
        (
            {"provider": "pixiv"},
            {"user": {"name": "絵師"}, "title": "作品"},
            ("pixiv", "絵師", "作品"),
        ),
        (
            {"provider": "generic:example.com", "url": "https://example.com/a"},
            {"category": "Generic", "id": "123", "title": "CON", "author": {"bad": "a"}},
            ("example.com", "投稿者不明", "123"),
        ),
        (
            {"url": "https://example.com/a"},
            {"category": "vimeo", "artist": ["音楽家"], "title": "../a/b:*"},
            ("vimeo", "音楽家", "a b"),
        ),
    ],
)
def test_output_names_across_sites(source, event, expected):
    from downloader_tools.publisher import output_names

    assert output_names(source, event, "abcdef") == expected


@pytest.mark.asyncio
async def test_readable_layout_reserves_collisions_and_preserves_existing_files(tmp_path):
    from pathlib import Path

    from PIL import Image

    from downloader_tools.publisher import PATH_LIMIT, Publisher, path_length
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "downloads"
        root.mkdir()
        await store.register_root("default", root)
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://x.com/user/status/123"],
                "profile_id": "gallery-default",
            },
            "layout",
        )
        source = await store.claim(batch["sources"][0]["id"])
        candidate = root / ".downloader/work" / source["id"] / "payload/a.png"
        candidate.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(candidate)
        publisher = Publisher(store)
        base = {"path": str(candidate), "title": "作品", "user": {"name": "作者"}}
        first = await publisher.prepare(source, {**base, "media_key": "post:1"})
        assert Path(first["final_path"]) == Path("X/作者/作品.png")
        second = await publisher.prepare(source, {**base, "media_key": "post:2"})
        assert Path(second["final_path"]).name.startswith("作品_")
        assert first["final_path"] != second["final_path"]
        duplicate = await publisher.prepare(
            source, {**base, "media_key": "post:1", "receipt_id": 99, "title": "改題"}
        )
        assert duplicate["final_path"] == first["final_path"]
        external = root / "X/作者/既存.png"
        external.parent.mkdir(parents=True)
        external.write_bytes(b"keep")
        third = await publisher.prepare(source, {**base, "media_key": "post:3", "title": "既存"})
        assert Path(third["final_path"]).name.startswith("既存_")
        await publisher.commit(third)
        assert external.read_bytes() == b"keep"
        long = await publisher.prepare(
            source,
            {
                **base,
                "media_key": "post:4",
                "title": "😀" * 200,
                "user": "長" * 200,
            },
        )
        assert path_length(str(root / long["final_path"])) <= PATH_LIMIT
        assert len(Path(long["final_path"]).parts) == 3
        upper = await publisher.prepare(
            source,
            {
                **base,
                "media_key": "post:unicode-1",
                "title": "Ä作品",
            },
        )
        lower = await publisher.prepare(
            source,
            {
                **base,
                "media_key": "post:unicode-2",
                "title": "a\u0308作品",
            },
        )
        assert Path(upper["final_path"]).name == "Ä作品.png"
        assert Path(lower["final_path"]).name.startswith("ä作品_")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_published_path_is_reused_when_title_changes(tmp_path):
    from PIL import Image

    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "output"
        root.mkdir()
        await store.register_root("default", root)
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "output_root_id": "default",
                "credential_id": None,
            },
            "a",
        )
        source = await store.claim(batch["sources"][0]["id"])
        candidate = root / ".downloader/work" / source["id"] / "payload/a.png"
        candidate.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(candidate)
        publisher = Publisher(store)
        event = {"path": str(candidate), "media_key": "site:1:1", "title": "最初の題名"}
        intent = await publisher.prepare(source, event)
        assert "最初の題名" in intent["final_path"]
        await publisher.commit(intent)
        renamed = await publisher.prepare(source, {**event, "title": "変更後の題名"})
        assert renamed["final_path"] == intent["final_path"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_durable_idempotency_and_shared_source(tmp_path):
    from downloader_tools.store import Store

    store = Store(tmp_path / "app.sqlite3")
    await store.start()
    try:
        request = {
            "kind": "video",
            "urls": ["https://example.com/a"],
            "profile_id": "video-default",
            "output_root_id": "default",
            "credential_id": None,
        }
        first = await store.create_batch(request, "same")
        assert await store.create_batch(request, "same") == first
        second = await store.create_batch(request, "different")
        assert first["sources"][0]["id"] == second["sources"][0]["id"]
        assert second["sources"][0]["shared"]
        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            await store.create_batch({**request, "urls": ["https://example.com/b"]}, "same")
    finally:
        await store.close()
    reopened = Store(tmp_path / "app.sqlite3")
    await reopened.start()
    assert len(await reopened.sources()) == 1
    await reopened.close()


@pytest.mark.asyncio
async def test_claim_fencing_and_stop_race(tmp_path):
    from downloader_tools.store import Store

    store = Store(tmp_path / "app.sqlite3")
    await store.start()
    try:
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "output_root_id": "default",
                "credential_id": None,
            },
            "a",
        )
        source_id = batch["sources"][0]["id"]
        claims = await asyncio.gather(store.claim(source_id), store.claim(source_id))
        assert sum(c is not None for c in claims) == 1
        claimed = next(c for c in claims if c is not None)
        await store.control(source_id, "pause")
        await store.finish(source_id, claimed["run_token"], 0, 0, True)
        source = await store.source(source_id)
        assert source["state"] == "paused"
        assert not await store.finish(source_id, "stale-token", 0, 0, True)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_publish_recovers_after_link_before_commit(tmp_path):
    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "output"
        root.mkdir()
        await store.register_root("default", root)
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "output_root_id": "default",
                "credential_id": None,
            },
            "a",
        )
        source = await store.claim(batch["sources"][0]["id"])
        from PIL import Image

        payload = root / ".downloader/work" / source["id"] / "payload"
        payload.mkdir(parents=True)
        candidate = payload / "a.png"
        Image.new("RGB", (4, 4), "red").save(candidate)
        publisher = Publisher(store)
        intent = await publisher.prepare(source, {"path": str(candidate), "media_key": "site:1:1"})
        final = root / intent["final_path"]
        final.parent.mkdir(parents=True)
        final.hardlink_to(candidate)
        await publisher.recover(root)
        assert len(await store.artifacts(source["id"])) == 1
        assert final.exists()
        await publisher.recover(root)
        assert len(await store.artifacts(source["id"])) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_publish_does_not_overwrite_conflicting_file(tmp_path):
    from PIL import Image

    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "output"
        root.mkdir()
        await store.register_root("default", root)
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "output_root_id": "default",
                "credential_id": None,
            },
            "a",
        )
        source = await store.claim(batch["sources"][0]["id"])
        path = root / ".downloader/work" / source["id"] / "payload/a.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(path)
        publisher = Publisher(store)
        intent = await publisher.prepare(source, {"path": str(path), "media_key": "site:1:1"})
        final = root / intent["final_path"]
        final.parent.mkdir(parents=True)
        final.write_bytes(b"keep")
        with pytest.raises(ValueError, match="OUTPUT_CONFLICT"):
            await publisher.commit(intent)
        assert final.read_bytes() == b"keep"
    finally:
        await store.close()
