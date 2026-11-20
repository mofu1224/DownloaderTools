import asyncio
import ipaddress
import json
import os
import shutil
import socket
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

import portalocker
import psutil

from .diagnostics import append_log
from .domain import TERMINAL
from .engines import build_command
from .events import Events
from .paths import atomic_json, private_directory
from .publisher import Publisher
from .store import Store


def select_next(queue: list[dict], active: list[dict], settings: dict, last_batch: str | None):
    if len(active) >= settings["total_slots"]:
        return None
    batches = list(dict.fromkeys(row["batch_id"] for row in queue))
    if last_batch in batches:
        offset = batches.index(last_batch) + 1
        batches = batches[offset:] + batches[:offset]
    for batch in batches:
        for source in queue:
            if source["batch_id"] != batch:
                continue
            if any(row["provider"] == source["provider"] for row in active):
                continue
            if source["provider"].startswith("generic:") and any(
                row["provider"].startswith("generic:") for row in active
            ):
                continue
            if (
                sum(row["kind"] == source["kind"] for row in active)
                >= settings[source["kind"] + "_slots"]
            ):
                continue
            if needs_postprocessing(source) and any(needs_postprocessing(row) for row in active):
                continue
            return source
    return None


def needs_postprocessing(source: dict) -> bool:
    return (
        source["kind"] == "video"
        or json.loads(source.get("profile", "{}")).get("media_policy") == "all"
    )


class Scheduler:
    def __init__(self, store: Store, root: Path, events: Events):
        self.store, self.root, self.events = store, root, events
        self.publisher = Publisher(store)
        self.active: dict[str, dict] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.progress: dict[str, dict] = {}
        self.root_locks: dict[str, portalocker.Lock] = {}
        self.blocked_roots: set[str] = set()
        self.wake = asyncio.Event()
        self.closing = False
        self.last_batch: str | None = None
        self.task: asyncio.Task | None = None
        self.error: str | None = None

    async def start(self):
        await self.recover()
        self.task = asyncio.create_task(self.loop(), name="scheduler")

    async def lock_root(self, root_id):
        root = await self.publisher.root(root_id)
        if root_id in self.blocked_roots:
            raise ValueError("OUTPUT_OWNERSHIP_UNRESOLVED")
        if root_id in self.root_locks:
            return root

        def acquire():
            root.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                import win32api

                volume = win32api.GetVolumeInformation(str(root.anchor))
                if volume[4] != "NTFS" or str(root).startswith("\\\\"):
                    raise ValueError("FILESYSTEM_UNSUPPORTED")
            elif sys.platform == "darwin":
                partitions = [
                    p
                    for p in psutil.disk_partitions(all=True)
                    if root.is_relative_to(Path(p.mountpoint))
                ]
                if (
                    not partitions
                    or max(partitions, key=lambda p: len(p.mountpoint)).fstype.lower() != "apfs"
                ):
                    raise ValueError("FILESYSTEM_UNSUPPORTED")
            else:
                raise ValueError("FILESYSTEM_UNSUPPORTED")
            private_directory(root / ".downloader")
            lock = portalocker.Lock(str(root / ".downloader/root.lock"), timeout=0)
            lock.acquire()
            try:
                probe = root / ".downloader/probe"
                target = root / ".downloader/probe-link"
                probe.write_bytes(b"probe")
                os.link(probe, target)
                try:
                    os.link(probe, target)
                except FileExistsError:
                    pass
                else:
                    raise ValueError("NON_OVERWRITE_UNSUPPORTED")
                target.unlink()
                probe.unlink()
            except BaseException:
                lock.release()
                raise
            return lock

        self.root_locks[root_id] = await asyncio.to_thread(acquire)
        return root

    async def recover(self):
        sources = await self.store.sources(active=True, limit=1000)
        quarantined = await self.store.read("SELECT * FROM output_quarantine")
        known = {s["id"] for s in sources}
        for row in quarantined:
            if row["source_id"] not in known:
                sources.append(await self.store.source(row["source_id"]))
                known.add(row["source_id"])
        quarantine_ids = {r["source_id"] for r in quarantined}
        pending = [
            s
            for s in sources
            if s["attempt_id"]
            and (
                s["state"] in ("running", "stopping", "recovering")
                or s["id"] in quarantine_ids
                or s["reason"] in ("OUTPUT_OWNERSHIP_UNRESOLVED", "HOST_EXIT_UNPROVEN")
            )
        ]
        for source in pending:
            safe = await self.ownership_safe(source)
            folder = (
                (await self.publisher.root(source["output_root_id"]))
                / ".downloader/work"
                / source["id"]
                / "receipts"
                / source["attempt_id"]
            )
            summary = folder / "exit.json"
            if summary.exists():
                result = json.loads(summary.read_text())
                safe = safe and bool(result.get("descendants_stopped"))
            if not safe:
                await self.quarantine(source, "OUTPUT_OWNERSHIP_UNRESOLVED")
            else:
                await self.store.write(
                    lambda c, s=source: c.execute(
                        "DELETE FROM output_quarantine WHERE source_id=?", (s["id"],)
                    )
                )
        self.blocked_roots = {
            r["output_root_id"]
            for r in await self.store.read("SELECT output_root_id FROM output_quarantine")
        }
        for source in sources:
            root = await self.publisher.root(source["output_root_id"])
            if source in pending:
                if source["output_root_id"] in self.blocked_roots:
                    await self.store.block(source["id"], "OUTPUT_OWNERSHIP_UNRESOLVED")
                    continue
                try:
                    await self.lock_root(source["output_root_id"])
                    await self.publisher.recover(root)
                    await self.publisher.check_catalog(source["id"])
                    result = await self.publish_receipts(source, root)
                    if result and not result.get("stopped"):
                        if result.get("protocol_error"):
                            await self.store.block(source["id"], "ENGINE_PROTOCOL_ERROR")
                            continue
                        await self.store.finish(
                            source["id"],
                            source["run_token"],
                            result["exit_code"],
                            result["failures"],
                            result["counts_complete"],
                        )
                        continue
                except OSError, ValueError, portalocker.LockException:
                    await self.store.block(source["id"], "PUBLICATION_RECOVERY_REQUIRED")
                    continue
                retries = source["crash_retries"] + 1
                attempts = await self.store.read(
                    "SELECT started_at FROM attempts WHERE id=?", (source["attempt_id"],)
                )
                spent = (result or {}).get(
                    "elapsed_seconds",
                    min(source["time_budget"], max(0, time.time() - attempts[0]["started_at"])),
                )
                exhausted = source["elapsed"] + spent >= source["time_budget"]
                state = (
                    "queued"
                    if source["desired_state"] == "run" and retries <= 1 and not exhausted
                    else "blocked"
                )
                if source["desired_state"] in ("pause", "cancel"):
                    state = "paused" if source["desired_state"] == "pause" else "cancelled"
                await self.store.write(
                    lambda c, s=source, r=retries, st=state, duration=spent, why="time_budget" if exhausted else "interrupted": (
                        c.execute(
                            "UPDATE attempts SET ended_at=?,result='interrupted' WHERE id=?",
                            (time.time(), s["attempt_id"]),
                        ),
                        c.execute(
                            "UPDATE sources SET state=?,phase=?,reason=?,crash_retries=?,elapsed=elapsed+?,revision=revision+1 "
                            "WHERE id=?",
                            (st, st, why, r, duration, s["id"]),
                        ),
                    )
                )
            elif (
                source["state"] == "paused"
                and source["reason"] == "shutdown"
                and source["desired_state"] == "run"
            ):
                await self.store.control(source["id"], "resume")
        protected = {
            r["source_id"] for r in await self.store.read("SELECT source_id FROM output_quarantine")
        }
        protected_attempts = {s["attempt_id"] for s in sources if s["id"] in protected}
        for folder in (self.root / "data/sessions").glob("*"):
            if folder.is_dir() and folder.name not in protected_attempts:
                await asyncio.to_thread(shutil.rmtree, folder)

    async def ownership_safe(self, source):
        owner_file = self.root / "data/instance" / (source["attempt_id"] + ".owner.json")
        if not owner_file.exists():
            recorded = await self.store.read(
                "SELECT owner FROM attempts WHERE id=?", (source["attempt_id"],)
            )
            return not bool(recorded and recorded[0]["owner"])
        owner = json.loads(owner_file.read_text())
        if owner.get("run_token") != source["run_token"]:
            return False
        for pid_key, created_key in (("pid", "created"), ("engine_pid", "engine_created")):
            if owner.get(pid_key):
                try:
                    process = psutil.Process(owner[pid_key])
                    if abs(process.create_time() - owner[created_key]) < 0.01:
                        if (
                            pid_key == "pid"
                            and owner.get("executable")
                            and os.path.normcase(process.exe())
                            != os.path.normcase(owner["executable"])
                        ):
                            return False
                        try:
                            await asyncio.to_thread(process.wait, 10)
                        except psutil.TimeoutExpired:
                            return False
                except psutil.NoSuchProcess:
                    pass
        if sys.platform == "darwin" and owner.get("engine_pid"):
            try:
                os.kill(-owner["engine_pid"], 0)
                return False
            except ProcessLookupError:
                pass
        return True

    async def quarantine(self, source, reason):
        self.blocked_roots.add(source["output_root_id"])
        await self.store.write(
            lambda c: c.execute(
                "INSERT INTO output_quarantine VALUES(?,?,?) "
                "ON CONFLICT(output_root_id,source_id) DO UPDATE SET reason=excluded.reason",
                (source["output_root_id"], source["id"], reason),
            )
        )
        await self.store.block(source["id"], reason)

    async def resolve_quarantine(self, root_id):
        rows = await self.store.read(
            "SELECT source_id FROM output_quarantine WHERE output_root_id=?", (root_id,)
        )
        for row in rows:
            source = await self.store.source(row["source_id"])
            path = self.root / "data/instance" / (source["attempt_id"] + ".owner.json")
            if not path.exists() or not await self.ownership_safe(source):
                raise ValueError("OUTPUT_OWNERSHIP_UNRESOLVED")
        await self.store.write(
            lambda c: c.execute("DELETE FROM output_quarantine WHERE output_root_id=?", (root_id,))
        )
        self.blocked_roots.discard(root_id)

    async def loop(self):
        while not self.closing:
            self.wake.clear()
            delay = 5 if self.active else None
            try:
                if self.store.fault:
                    self.error = "DB_ERROR"
                    for row in self.active.values():
                        row["stop_reason"] = "DB_ERROR"
                        await self.stop_process(row)
                else:
                    settings = await self.store.settings()
                    now = time.time()
                    limits = {
                        r["provider"]: r["cooldown_until"]
                        for r in await self.store.read("SELECT * FROM provider_limits")
                    }
                    for timestamp in limits.values():
                        if timestamp > now:
                            delay = min(delay or 1800, timestamp - now)
                    queued = [
                        s
                        for s in reversed(await self.store.sources(active=True, limit=1000))
                        if s["state"] == "queued"
                    ]
                    for source in queued:
                        if source["next_eligible_at"] > now:
                            delay = min(delay or 1800, source["next_eligible_at"] - now)
                    candidates = [
                        s
                        for s in queued
                        if s["next_eligible_at"] <= now and limits.get(s["provider"], 0) <= now
                    ]
                    while chosen := select_next(
                        candidates, list(self.active.values()), settings, self.last_batch
                    ):
                        candidates.remove(chosen)
                        try:
                            root = await self.lock_root(chosen["output_root_id"])
                            if shutil.disk_usage(root).free < settings["reserve_bytes"]:
                                raise ValueError("DISK_SPACE_LOW")
                            await self.publisher.recover(root)
                            if chosen["attempt_id"]:
                                await self.publish_receipts(chosen, root)
                                await self.publisher.check_catalog(chosen["id"])
                            tools = await self.store.read(
                                "SELECT t.manifest FROM toolsets t JOIN toolset_validation v ON v.id=t.id "
                                "WHERE t.id=? AND v.verified=1",
                                (chosen["toolset_id"],),
                            )
                            if not tools:
                                raise ValueError("TOOLSET_UNAVAILABLE")
                            async with asyncio.timeout(10):
                                addresses = await asyncio.to_thread(
                                    socket.getaddrinfo, urlsplit(chosen["url"]).hostname, None
                                )

                            if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                                raise ValueError("URL_LOCAL_ADDRESS")
                            source = await self.store.claim(chosen["id"])
                            if source is None:
                                continue
                            source["manifest"] = json.loads(tools[0]["manifest"])
                            self.active[source["id"]] = source
                            self.last_batch = source["batch_id"]
                            self.workers[source["id"]] = asyncio.create_task(self.run(source, root))
                            self.events.publish({"type": "refresh"})
                        except (
                            OSError,
                            ValueError,
                            portalocker.LockException,
                            TimeoutError,
                        ) as exc:
                            await asyncio.to_thread(
                                append_log,
                                self.root / "data/logs" / (chosen["id"] + ".log"),
                                "scheduler",
                                traceback.format_exc(),
                            )
                            await self.store.block(
                                chosen["id"],
                                str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                            )
                            self.events.publish({"type": "refresh"})
                    for source in list(self.active.values()):
                        current = await self.store.source(source["id"])
                        if self.closing or current["desired_state"] != "run":
                            await self.stop_process(source)
                        if (
                            time.monotonic()
                            - source.get("started", time.monotonic())
                            + current["elapsed"]
                            >= current["time_budget"]
                        ):
                            source["stop_reason"] = "time_budget"
                            await self.stop_process(source)
                        if (
                            shutil.disk_usage(
                                await self.publisher.root(source["output_root_id"])
                            ).free
                            < settings["reserve_bytes"]
                        ):
                            source["stop_reason"] = "DISK_SPACE_LOW"
                            await self.stop_process(source)
            except Exception as exc:
                self.error = type(exc).__name__
                await asyncio.to_thread(
                    append_log,
                    self.root / "data/logs/scheduler.log",
                    "scheduler",
                    traceback.format_exc(),
                )
                delay = 5
            if self.active:
                delay = min(delay or 5, 5)
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except TimeoutError:
                pass

    async def stop_process(self, source):
        process = source.get("process")
        if process and process.returncode is None and not source.get("stop_sent"):
            try:
                process.stdin.write(b'{"action":"stop"}\n')
                await process.stdin.drain()
                source["stop_sent"] = True
            except BrokenPipeError, ConnectionError:
                pass

    async def run(self, source, root):
        source_id = source["id"]
        reason = None
        result = None
        stderr_task = None
        log_path = self.root / "data/logs" / (source_id + ".log")

        async def log(text):
            await asyncio.to_thread(append_log, log_path, "scheduler", text)

        async def drain_stderr(stream):
            while line := await stream.readline():
                await log("host_stderr " + line.decode("utf-8", errors="replace"))

        try:
            await log(f"attempt_claimed attempt={source['attempt_id']} kind={source['kind']}")
            work = root / ".downloader/work" / source_id
            receipts = work / "receipts" / source["attempt_id"]
            cookie = None
            if source["credential_id"]:
                rows = await self.store.read(
                    "SELECT path FROM credentials WHERE id=?", (source["credential_id"],)
                )
                if not rows or not rows[0]["path"]:
                    raise ValueError("CREDENTIAL_MISSING")
                session = self.root / "data/sessions" / source["attempt_id"]
                private_directory(session)
                cookie = session / "cookies.txt"
                await asyncio.to_thread(shutil.copyfile, self.root / rows[0]["path"], cookie)
            command = build_command(source, source["manifest"], work, cookie)
            config = {
                "source_id": source_id,
                "attempt_id": source["attempt_id"],
                "run_token": source["run_token"],
                "owner_path": str(
                    self.root / "data/instance" / (source["attempt_id"] + ".owner.json")
                ),
                "receipts": str(receipts),
                "command": command,
                "log_path": str(self.root / "data/logs" / (source_id + ".log")),
            }
            config_path = self.root / "data/sessions" / source["attempt_id"] / "run.json"
            private_directory(config_path.parent)
            atomic_json(config_path, config)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "downloader_tools.source_host",
                str(config_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=65537,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            source["process"] = process
            stderr_task = asyncio.create_task(drain_stderr(process.stderr))
            source["started"] = time.monotonic()
            assert process.stdout and process.stdin
            async with asyncio.timeout(10):
                ready = json.loads(await process.stdout.readline())
            if ready.get("type") != "ready" or ready.get("run_token") != source["run_token"]:
                raise ValueError("HOST_HANDSHAKE_INVALID")
            await self.store.write(
                lambda c: c.execute(
                    "UPDATE attempts SET owner=? WHERE id=?",
                    (json.dumps(ready["owner"]), source["attempt_id"]),
                )
            )
            process.stdin.write(b'{"action":"start"}\n')
            await process.stdin.drain()
            checkpoint = 0.0
            seq = 0
            while line := await process.stdout.readline():
                message = json.loads(line)
                if message.get("run_token") != source["run_token"] or message.get("seq", 0) <= seq:
                    continue
                seq = message["seq"]
                if message["type"] == "item_ready":
                    await log(f"publication_start receipt={message['receipt_id']}")
                    await self.publish_event(source, message)
                    await log(f"publication_finished receipt={message['receipt_id']}")
                    receipt = receipts / f"item-{int(message['receipt_id']):08d}.json"
                    await asyncio.to_thread(receipt.unlink, missing_ok=True)
                    try:
                        process.stdin.write(
                            (
                                json.dumps({"action": "ack", "receipt_id": message["receipt_id"]})
                                + "\n"
                            ).encode()
                        )
                        await process.stdin.drain()
                    except BrokenPipeError, ConnectionResetError, ConnectionAbortedError:
                        # 高速な単一項目では公開検証中にHostが正常終了する。receiptが正本。
                        pass
                    self.events.publish({"type": "refresh"})
                elif message["type"] == "progress":
                    self.progress[source_id] = message
                    self.events.publish(
                        {"type": "progress", "source_id": source_id, "progress": message}
                    )
                    if time.monotonic() - checkpoint >= 5:
                        await self.store.write(
                            lambda c, m=message: c.execute(
                                "UPDATE sources SET progress=?,phase=? WHERE id=? AND run_token=? AND state='running'",
                                (json.dumps(m), m["phase"], source_id, source["run_token"]),
                            )
                        )
                        checkpoint = time.monotonic()
            await process.wait()
            result = await self.publish_receipts(source, root)
            if not result or not result.get("descendants_stopped"):
                self.blocked_roots.add(source["output_root_id"])
                reason = "HOST_EXIT_UNPROVEN"
            elif result.get("protocol_error"):
                reason = "ENGINE_PROTOCOL_ERROR"
            elif result.get("backlog"):
                reason = "publish_backlog"
            elif result.get("stalled"):
                reason = "ENGINE_STALLED"
            if result and result.get("rate_limited"):
                await self.store.write(
                    lambda c: c.execute(
                        "INSERT INTO provider_limits VALUES(?,?,1,'HTTP_429') "
                        "ON CONFLICT(provider) DO UPDATE SET strikes=strikes+1, cooldown_until=?+min(1800,60*(1<<min(strikes,5)))",
                        (source["provider"], time.time() + 60, time.time()),
                    )
                )
        except Exception as exc:
            await log(traceback.format_exc())
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if isinstance(exc, OSError):
                reason = f"IO_ERROR_{exc.errno}_{getattr(exc, 'winerror', None)}"
            process = source.get("process")
            if process and process.returncode is None:
                process.stdin.close()
                try:
                    async with asyncio.timeout(12):
                        await process.wait()
                except TimeoutError:
                    self.blocked_roots.add(source["output_root_id"])
                    reason = "HOST_EXIT_UNPROVEN"
        finally:
            reason = source.get("stop_reason") or ("shutdown" if self.closing else reason)
            if result and result.get("stopped") and not reason:
                current = await self.store.source(source_id)
                if current["desired_state"] == "run":
                    reason = (
                        "restart_after_stop" if source.get("stop_sent") else "ENGINE_INTERRUPTED"
                    )
            try:
                await self.store.finish(
                    source_id,
                    source["run_token"],
                    (result or {}).get("exit_code", 1),
                    (result or {}).get("failures", 1),
                    (result or {}).get("counts_complete", False),
                    reason,
                )
                if reason == "HOST_EXIT_UNPROVEN":
                    await self.quarantine(source, reason)
                if reason == "publish_backlog":
                    await self.store.control(source_id, "resume")
                current = await self.store.source(source_id)
                await log(
                    f"attempt_finished state={current['state']} reason={reason or '-'} "
                    f"exit_code={(result or {}).get('exit_code', 1)} "
                    f"completed={current['completed_count']} retries={current['crash_retries']}"
                )
                if (
                    current["state"] in TERMINAL
                    and source["output_root_id"] not in self.blocked_roots
                ):
                    try:
                        await self.publisher.cleanup_links(source_id)
                    except OSError, ValueError:
                        self.error = "WORK_CLEANUP_PENDING"
            finally:
                if stderr_task:
                    if not stderr_task.done():
                        stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                self.active.pop(source_id, None)
                self.workers.pop(source_id, None)
                self.progress.pop(source_id, None)
                self.events.publish({"type": "refresh"})
                self.wake.set()
                session = self.root / "data/sessions" / source["attempt_id"]
                if source["output_root_id"] not in self.blocked_roots and session.exists():
                    await asyncio.to_thread(shutil.rmtree, session)

    async def publish_receipts(self, source, root):
        folder = root / ".downloader/work" / source["id"] / "receipts" / source["attempt_id"]
        # 未公開候補はAttemptごとに最大1,000件。成功したreceiptだけ削除する。
        for path in sorted(folder.glob("item-*.json")):
            event = json.loads(path.read_text(encoding="utf-8"))
            if event.get("run_token") != source["run_token"]:
                raise ValueError("RECEIPT_TOKEN_MISMATCH")
            await self.publish_event(source, event)
            await asyncio.to_thread(path.unlink)
        summary = folder / "exit.json"
        if summary.exists():
            result = json.loads(summary.read_text())
            if result.get("run_token") == source["run_token"]:
                return result
        return None

    async def publish_event(self, source, event):
        category, item_id, asset = event.get("category"), event.get("id"), event.get("asset")
        identity = event.get("identity")
        if (
            isinstance(identity, str)
            and len(identity) == 64
            and all(letter in "0123456789abcdef" for letter in identity)
        ):
            key = ["asset", identity]
        elif category and item_id is not None:
            key = [category, str(item_id), str(asset)]
        else:
            root = await self.publisher.root(source["output_root_id"])
            relative = Path(event["path"]).relative_to(
                root / ".downloader/work" / source["id"] / "payload"
            )
            key = ["source", source["id"], str(relative)]
        event["media_key"] = json.dumps(key, ensure_ascii=True, separators=(",", ":"))
        outcome = await self.publisher.publish(source, event)
        if outcome:
            await asyncio.to_thread(
                append_log,
                self.root / "data/logs" / (source["id"] + ".log"),
                "scheduler",
                f"policy_skip reason={outcome} receipt={event.get('receipt_id', '-')}",
            )
        return outcome

    async def close(self):
        self.closing = True
        self.wake.set()
        for row in list(self.active.values()):
            await self.stop_process(row)
        if self.task:
            await self.task
        if self.workers:
            await asyncio.wait(list(self.workers.values()), timeout=15)
        for lock in self.root_locks.values():
            lock.release()
