import asyncio
import hashlib
import json

import pytest


async def queued(store, kind="gallery", key="a"):
    return (
        await store.create_batch(
            {"kind": kind, "urls": ["https://example.com/" + key], "profile_id": kind + "-default"},
            key,
        )
    )["sources"][0]["id"]


@pytest.mark.asyncio
async def test_output_ownership_quarantine_survives_restart(tmp_path, monkeypatch):
    import psutil

    from downloader_tools.events import Events
    from downloader_tools.paths import atomic_json
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    scheduler = Scheduler(store, tmp_path, Events())
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        source = await store.claim(await queued(store))
        await store.block(source["id"], "OUTPUT_OWNERSHIP_UNRESOLVED")
        atomic_json(
            tmp_path / "data/instance" / (source["attempt_id"] + ".owner.json"),
            {"run_token": source["run_token"], "pid": 1234, "created": 42},
        )

        class LivingProcess:
            def __init__(self, pid):
                pass

            def create_time(self):
                return 42

            def wait(self, timeout):
                raise psutil.TimeoutExpired(timeout)

        monkeypatch.setattr(psutil, "Process", LivingProcess)
        await scheduler.recover()
        assert "default" in scheduler.blocked_roots
    finally:
        await scheduler.close()
        await store.close()


@pytest.mark.asyncio
async def test_recovery_never_succeeds_without_descendant_exit_proof(tmp_path):
    from downloader_tools.events import Events
    from downloader_tools.paths import atomic_json
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    scheduler = Scheduler(store, tmp_path, Events())
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        source = await store.claim(await queued(store))
        atomic_json(
            output
            / ".downloader/work"
            / source["id"]
            / "receipts"
            / source["attempt_id"]
            / "exit.json",
            {
                "run_token": source["run_token"],
                "exit_code": 0,
                "failures": 0,
                "counts_complete": True,
                "stopped": False,
                "descendants_stopped": False,
            },
        )
        await scheduler.recover()
        assert (await store.source(source["id"]))["state"] == "blocked"
        assert "default" in scheduler.blocked_roots
    finally:
        await scheduler.close()
        await store.close()


@pytest.mark.asyncio
async def test_tampered_active_toolset_is_not_left_executable(tmp_path):
    from downloader_tools.store import Store
    from downloader_tools.toolsets import Toolsets

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        folder = tmp_path / "runtime/toolsets/one"
        folder.mkdir(parents=True)
        binary = folder / "binary"
        binary.write_bytes(b"verified")
        rel = str(binary.relative_to(tmp_path))
        manifest = {
            "id": "one",
            "python": rel,
            "ffmpeg": rel,
            "ffprobe": rel,
            "node": rel,
            "contracts": 1,
            "versions": {},
            "hashes": {rel: hashlib.sha256(b"verified").hexdigest()},
        }
        (folder / "manifest.json").write_text(json.dumps(manifest))
        tools = Toolsets(store, tmp_path)
        await tools.register(manifest)
        await tools.activate("one")
        binary.write_bytes(b"tampered")
        await tools.discover()
        assert not await store.read("SELECT id FROM toolsets WHERE active=1")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_replayed_receipt_does_not_change_completed_or_skipped_counts(tmp_path):
    from PIL import Image

    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        source = await store.claim(await queued(store))
        candidate = output / ".downloader/work" / source["id"] / "payload/a.png"
        candidate.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(candidate)
        event = {"path": str(candidate), "media_key": "site:1:1", "receipt_id": 1}
        publisher = Publisher(store)
        await publisher.publish(source, event)
        await publisher.publish(source, event)
        actual = await store.source(source["id"])
        assert (actual["completed_count"], actual["skipped_count"]) == (1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dns_timeout_does_not_starve_another_provider(tmp_path, monkeypatch):
    import socket

    from downloader_tools.events import Events
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    scheduler = Scheduler(store, tmp_path, Events())
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        await store.write(
            lambda c: (
                c.execute("INSERT INTO toolsets VALUES('fixture','{}',1)"),
                c.execute("INSERT INTO toolset_validation VALUES('fixture',1)"),
            )
        )
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://bad.example.com/a", "https://good.example.org/b"],
                "profile_id": "gallery-default",
            },
            "dns",
        )

        def resolve(host, port):
            if host == "bad.example.com":
                raise TimeoutError("fixture DNS deadline")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        started = asyncio.Event()

        async def worker(source, root):
            started.set()
            scheduler.closing = True
            scheduler.active.pop(source["id"])
            scheduler.workers.pop(source["id"])
            scheduler.wake.set()

        monkeypatch.setattr(scheduler, "run", worker)
        scheduler.task = asyncio.create_task(scheduler.loop())
        await asyncio.wait_for(started.wait(), 3)
        assert (await store.source(batch["sources"][0]["id"]))["state"] == "blocked"
    finally:
        await scheduler.close()
        await store.close()


@pytest.mark.asyncio
async def test_startup_removes_stale_session_cookies(tmp_path):
    from downloader_tools.events import Events
    from downloader_tools.scheduler import Scheduler
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        stale = tmp_path / "data/sessions/stale/cookies.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("private")
        await Scheduler(store, tmp_path, Events()).recover()
        assert not stale.exists()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_missing_committed_artifact_is_invalidated(tmp_path):
    from PIL import Image

    from downloader_tools.publisher import Publisher
    from downloader_tools.store import Store

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        output = tmp_path / "downloads"
        output.mkdir()
        await store.register_root("default", output)
        source = await store.claim(await queued(store))
        candidate = output / ".downloader/work" / source["id"] / "payload/a.png"
        candidate.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(candidate)
        publisher = Publisher(store)
        await publisher.publish(source, {"path": str(candidate), "media_key": "site:1:1"})
        artifact = (await store.artifacts(source["id"]))[0]
        (output / artifact["relative_path"]).unlink()
        await publisher.check_catalog(source["id"])
        assert (await store.artifacts(source["id"]))[0]["valid"] == 0
    finally:
        await store.close()
