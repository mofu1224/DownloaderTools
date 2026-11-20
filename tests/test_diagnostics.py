import asyncio
import json
import sys

import pytest


async def run_host(tmp_path, code, **options):
    from downloader_tools.paths import atomic_json

    config = tmp_path / "run.json"
    atomic_json(
        config,
        {
            "source_id": "s",
            "attempt_id": "a",
            "run_token": "private-token",
            "owner_path": str(tmp_path / "owner.json"),
            "receipts": str(tmp_path / "receipts"),
            "log_path": str(tmp_path / "source.log"),
            "command": [sys.executable, "-c", code],
            **options,
        },
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "downloader_tools.source_host",
        str(config),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout
    assert json.loads(await process.stdout.readline())["type"] == "ready"
    process.stdin.write(b'{"action":"start"}\n')
    await process.stdin.drain()
    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(process.stdout.read(), process.stderr.read(), process.wait()), 15
        )
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 0, stderr.decode(errors="replace")
    return json.loads((tmp_path / "receipts/exit.json").read_text()), stdout


async def test_host_stops_unresponsive_engine_and_records_reason(tmp_path):
    result, _ = await run_host(
        tmp_path, "import time; time.sleep(60)", idle_timeout=0.8, monitor_interval=0.1
    )
    assert result["stalled"] and result["stopped"] and result["descendants_stopped"]
    log = (tmp_path / "source.log").read_text(encoding="utf-8")
    assert "ENGINE_STALLED" in log and "engine_exit" in log
    assert "private-token" not in log


async def test_host_does_not_stop_silent_engine_that_is_writing(tmp_path):
    code = (
        "import time; from pathlib import Path; "
        f"p=Path({str(tmp_path / 'media.part')!r}); "
        "f=p.open('wb'); "
        "[(f.write(b'x'*8192),f.flush(),time.sleep(.1)) for _ in range(25)]; f.close()"
    )
    result, _ = await run_host(tmp_path, code, idle_timeout=0.8, monitor_interval=0.1)
    assert not result["stalled"] and result["exit_code"] == 0


async def test_host_logs_structured_progress_and_redacts_engine_output(tmp_path):
    code = (
        "import json; "
        "print('DT_PROGRESS\\t'+json.dumps({'status':'downloading','downloaded_bytes':123})); "
        "print('request https://example.com/private?token=abc'); "
        "print('Authorization: Bearer hidden-value')"
    )
    result, _ = await run_host(tmp_path, code)
    assert result["exit_code"] == 0
    log = (tmp_path / "source.log").read_text(encoding="utf-8")
    assert "progress" in log and "123" in log and "engine_exit" in log
    assert "hidden-value" not in log and "token=abc" not in log
    assert "[" in log and "stdout" in log


@pytest.mark.asyncio
async def test_stall_retry_is_bounded_and_obeys_pause_and_budget(tmp_path):
    from downloader_tools.store import Store

    store = Store(tmp_path / "app.sqlite3")
    await store.start()
    try:
        batch = await store.create_batch(
            {"kind": "video", "urls": ["https://example.com/a"], "profile_id": "video-default"}, "a"
        )
        identity = batch["sources"][0]["id"]
        for retry in range(3):
            await store.write(
                lambda c: c.execute("UPDATE sources SET next_eligible_at=0 WHERE id=?", (identity,))
            )
            source = await store.claim(identity)
            await store.finish(identity, source["run_token"], 1, 0, False, "ENGINE_STALLED")
            current = await store.source(identity)
            assert current["state"] == ("queued" if retry < 2 else "blocked")
        assert current["crash_retries"] == 2
        await store.control(identity, "resume")
        source = await store.claim(identity)
        await store.control(identity, "pause")
        await store.finish(identity, source["run_token"], 1, 0, False, "ENGINE_STALLED")
        assert (await store.source(identity))["state"] == "paused"
        await store.control(identity, "resume")
        await store.write(
            lambda c: c.execute(
                "UPDATE sources SET elapsed=time_budget,crash_retries=0 WHERE id=?", (identity,)
            )
        )
        source = await store.claim(identity)
        await store.finish(identity, source["run_token"], 1, 0, False, "ENGINE_STALLED")
        assert (await store.source(identity))["state"] == "blocked"
    finally:
        await store.close()


def test_watchdog_recognizes_cpu_child_changes_and_unknown_activity():
    from downloader_tools.diagnostics import activity_changed

    before = {(1, 1.0): (0.0, 0, 0)}
    assert not activity_changed(before, before)
    assert activity_changed(before, {(1, 1.0): (0.5, 0, 0)})
    assert activity_changed(before, {(1, 1.0): (0.0, 8192, 0)})
    assert activity_changed(before, {(1, 1.0): (0.0, 0, 0), (2, 2.0): (0.0, 0, 0)})
    assert activity_changed(before, None)


async def test_scheduler_requeues_stalled_host_and_preserves_partial_file(tmp_path, monkeypatch):
    import downloader_tools.scheduler as module
    from downloader_tools.events import Events
    from downloader_tools.paths import atomic_json
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    scheduler = module.Scheduler(store, tmp_path, Events())
    root = tmp_path / "downloads"
    root.mkdir()
    await store.register_root("default", root)
    try:
        batch = await store.create_batch(
            {"kind": "video", "urls": ["https://example.com/a"], "profile_id": "video-default"}, "a"
        )
        source = await store.claim(batch["sources"][0]["id"])
        source["manifest"] = {}
        partial = root / ".downloader/work" / source["id"] / "payload/media.part"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"already downloaded")
        monkeypatch.setattr(
            module,
            "build_command",
            lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
        )

        def configure(path, config):
            atomic_json(path, {**config, "idle_timeout": 0.8, "monitor_interval": 0.1})

        monkeypatch.setattr(module, "atomic_json", configure)
        await asyncio.wait_for(scheduler.run(source, root), 15)
        current = await store.source(source["id"])
        assert current["state"] == "queued" and current["crash_retries"] == 1
        assert current["reason"] == "ENGINE_STALLED"
        assert partial.read_bytes() == b"already downloaded"
        log = (tmp_path / "data/logs" / (source["id"] + ".log")).read_text(encoding="utf-8")
        assert "attempt_finished state=queued" in log
    finally:
        await scheduler.close()
        await store.close()


async def test_scheduler_wakes_for_delayed_retry_without_another_request(tmp_path, monkeypatch):
    import socket
    import time

    from downloader_tools.events import Events
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    scheduler = Scheduler(store, tmp_path, Events())
    try:
        root = tmp_path / "downloads"
        root.mkdir()
        await store.register_root("default", root)
        await store.write(
            lambda c: (
                c.execute("INSERT INTO toolsets VALUES('fixture','{}',1)"),
                c.execute("INSERT INTO toolset_validation VALUES('fixture',1)"),
            )
        )
        batch = await store.create_batch(
            {"kind": "video", "urls": ["https://example.com/a"], "profile_id": "video-default"}, "a"
        )
        identity = batch["sources"][0]["id"]
        eligible = time.time() + 0.3
        await store.write(
            lambda c: c.execute(
                "UPDATE sources SET next_eligible_at=? WHERE id=?", (eligible, identity)
            )
        )
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
        )
        started = asyncio.Event()

        async def worker(source, root):
            assert time.time() >= eligible
            started.set()
            scheduler.closing = True
            scheduler.active.pop(source["id"])
            scheduler.workers.pop(source["id"])
            scheduler.wake.set()

        monkeypatch.setattr(scheduler, "run", worker)
        scheduler.task = asyncio.create_task(scheduler.loop())
        await asyncio.wait_for(started.wait(), 3)
    finally:
        await scheduler.close()
        await store.close()


async def test_gallery_attachment_zip_is_validated_and_published_without_extraction(tmp_path):
    import io
    import zipfile

    from PIL import Image

    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        root = tmp_path / "downloads"
        root.mkdir()
        await store.register_root("default", root)
        profile = await store.add_profile({"kind": "gallery", "media_policy": "all"})
        batch = await store.create_batch(
            {"kind": "gallery", "urls": ["https://example.com/album"], "profile_id": profile["id"]},
            "zip",
        )
        source = await store.claim(batch["sources"][0]["id"])
        payload = root / ".downloader/work" / source["id"] / "payload"
        payload.mkdir(parents=True)
        candidate = payload / "album.zip"
        image = io.BytesIO()
        Image.new("RGB", (4, 4)).save(image, "JPEG")
        with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../outside.jpg", image.getvalue())
            archive.writestr("nested/image.jpg", image.getvalue())
        publisher = Publisher(store)
        await publisher.publish(
            source,
            {
                "path": str(candidate),
                "media_key": "album:1",
                "receipt_id": 1,
                "category": "example",
                "uploader": "作者",
                "title": "画像集",
            },
        )
        artifacts = await store.artifacts(source["id"])
        assert len(artifacts) == 1
        published = root / artifacts[0]["relative_path"]
        assert published.suffix == ".zip" and published.read_bytes() == candidate.read_bytes()
        assert not list(root.rglob("*.jpg"))
        # CRC不一致を検出し、壊れたZIPを公開しない。
        corrupt = payload / "corrupt.zip"
        with zipfile.ZipFile(corrupt, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("image.jpg", b"unique-payload")
        corrupt.write_bytes(corrupt.read_bytes().replace(b"unique-payload", b"broken-payload"))
        with pytest.raises(ValueError, match="ARCHIVE_INVALID"):
            await publisher.validate(corrupt, source)
        broken_image = payload / "broken.jpg"
        broken_image.write_bytes(b"not an image")
        with pytest.raises(ValueError, match="IMAGE_INVALID"):
            await publisher.validate(broken_image, source)
    finally:
        await store.close()
