import asyncio
import json
import sys
import zipfile

import pytest
from PIL import Image

from downloader_tools.events import Events
from downloader_tools.publisher import Publisher
from downloader_tools.scheduler import Scheduler
from downloader_tools.store import Store


async def setup_source(tmp_path):
    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    output = tmp_path / "downloads"
    output.mkdir()
    await store.register_root("default", output)
    batch = await store.create_batch(
        {"kind": "gallery", "urls": ["https://example.com/album"], "profile_id": "gallery-default"},
        "policy",
    )
    source = await store.claim(batch["sources"][0]["id"])
    payload = output / ".downloader/work" / source["id"] / "payload"
    payload.mkdir(parents=True)
    return store, source, output, payload


@pytest.mark.parametrize("only_skips", [True, False])
async def test_policy_skip_is_acknowledged_and_source_finishes_successfully(
    tmp_path, monkeypatch, only_skips
):
    import downloader_tools.scheduler as module

    store, source, output, payload = await setup_source(tmp_path)
    scheduler = Scheduler(store, tmp_path, Events())
    try:
        skip = {
            "path": "",
            "category": "example",
            "id": "zip",
            "asset": 0,
            "policy_skip": "MEDIA_POLICY_IMAGES_ONLY",
        }
        events = [skip]
        if not only_skips:
            image = payload / "next.png"
            Image.new("RGB", (4, 4)).save(image)
            events.append({"path": str(image), "category": "example", "id": "image", "asset": 0})
        source["manifest"] = {}
        code = "import json; events=json.loads(" + repr(json.dumps(events)) + "); "
        code += "[print('DT_ITEM\\t'+json.dumps(e),flush=True) for e in events]"
        monkeypatch.setattr(module, "build_command", lambda *args: [sys.executable, "-c", code])
        await asyncio.wait_for(scheduler.run(source, output), 15)
        current = await store.source(source["id"])
        assert current["state"] == "succeeded" and current["reason"] is None
        assert current["skipped_count"] == 1
        assert current["completed_count"] == (0 if only_skips else 1)
        await scheduler.publish_event(source, skip)
        assert (await store.source(source["id"]))["skipped_count"] == 1
        log = (tmp_path / "data/logs" / (source["id"] + ".log")).read_text(encoding="utf-8")
        assert "policy_skip" in log
    finally:
        await scheduler.close()
        await store.close()


@pytest.mark.parametrize("suffix", [".zip", ".bin"])
async def test_downloaded_zip_skips_without_blocking_next_image(tmp_path, suffix):
    store, source, output, payload = await setup_source(tmp_path)
    publisher = Publisher(store)
    try:
        archive = payload / ("archive" + suffix)
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("one.jpg", b"attachment")
        event = {"path": str(archive), "media_key": "archive", "receipt_id": 1}
        assert await publisher.publish(source, event) == "MEDIA_POLICY_IMAGES_ONLY"
        await publisher.publish(source, event)
        image = payload / "next.png"
        Image.new("RGB", (4, 4)).save(image)
        await publisher.publish(source, {"path": str(image), "media_key": "next", "receipt_id": 2})
        current = await store.source(source["id"])
        assert (current["completed_count"], current["skipped_count"]) == (1, 1)
        assert len(await store.artifacts(source["id"])) == 1
        broken = payload / "broken.png"
        broken.write_bytes(b"invalid image")
        with pytest.raises(ValueError, match="IMAGE_INVALID"):
            await publisher.publish(
                source, {"path": str(broken), "media_key": "broken", "receipt_id": 3}
            )
    finally:
        await store.close()
