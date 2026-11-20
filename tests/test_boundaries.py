import asyncio
import json
import threading

import pytest


@pytest.mark.asyncio
async def test_cancelled_db_caller_does_not_release_pending_slot_early(tmp_path):
    from downloader_tools.store import Store

    store = Store(tmp_path / "app.sqlite3")
    await store.start()
    release = threading.Event()
    entered = threading.Event()

    def transaction(conn):
        entered.set()
        release.wait(3)
        conn.execute("UPDATE settings SET body=body WHERE id=1")

    request = asyncio.create_task(store.write(transaction))
    await asyncio.to_thread(entered.wait, 2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    try:
        assert store.pending == 1
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_paused_then_resumed_during_stop_is_requeued(tmp_path):
    from downloader_tools.store import Store

    store = Store(tmp_path / "app.sqlite3")
    await store.start()
    try:
        batch = await store.create_batch(
            {"kind": "video", "urls": ["https://example.com/a"], "profile_id": "video-default"}, "k"
        )
        identity = batch["sources"][0]["id"]
        source = await store.claim(identity)
        await store.control(identity, "pause")
        await store.control(identity, "resume")
        await store.finish(identity, source["run_token"], 1, 0, False, "restart_after_stop")
        assert (await store.source(identity))["state"] == "queued"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_slow_sse_reader_is_disconnected_without_unbounded_queue():
    from downloader_tools.events import Events

    events = Events()
    async with events.subscribe() as queue:
        for n in range(300):
            events.publish({"type": "state", "revision": n})
        assert queue.qsize() == 1
        assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_sourcehost_streams_more_than_1000_receipts_when_acknowledged(tmp_path):
    import sys

    from downloader_tools.paths import atomic_json

    command = [
        sys.executable,
        "-c",
        "import json,time; [(print('DT_ITEM\\t'+json.dumps({'path':'a.png','category':'test','id':i,'asset':0}),flush=True),time.sleep(.001)) for i in range(1010)]",
    ]
    config = tmp_path / "run.json"
    atomic_json(
        config,
        {
            "source_id": "s",
            "attempt_id": "a",
            "run_token": "t",
            "owner_path": str(tmp_path / "owner.json"),
            "receipts": str(tmp_path / "receipts"),
            "log_path": str(tmp_path / "log.txt"),
            "command": command,
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
    assert process.stdout and process.stdin
    assert json.loads(await process.stdout.readline())["type"] == "ready"
    process.stdin.write(b'{"action":"start"}\n')
    await process.stdin.drain()
    received = 0
    while line := await asyncio.wait_for(process.stdout.readline(), 15):
        event = json.loads(line)
        if event["type"] == "item_ready":
            received += 1
            process.stdin.write(
                (json.dumps({"action": "ack", "receipt_id": event["receipt_id"]}) + "\n").encode()
            )
            await process.stdin.drain()
    await process.wait()
    assert received == 1010
    result = json.loads((tmp_path / "receipts/exit.json").read_text())
    assert result["counts_complete"] and not result["backlog"]
