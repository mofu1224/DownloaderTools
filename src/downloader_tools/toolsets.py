import asyncio
import hashlib
import json
import sys
from pathlib import Path

from .database_driver import sqlite3
from .paths import contained
from .store import Store


class Toolsets:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, root
        self.status: dict[str, dict] = {}
        self.update_task: asyncio.Task | None = None

    async def update(self):
        from .store import uid

        if self.update_task and not self.update_task.done():
            raise ValueError("UPDATE_IN_USE")
        identity = uid()
        self.status[identity] = {"state": "running"}
        while len(self.status) > 100:
            del self.status[next(iter(self.status))]

        async def build():
            process = None
            result_file = self.root / ".cache" / ("update-" + identity + ".json")
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(self.root / "scripts/build_toolset.py"),
                    "--refresh",
                    "--result-file",
                    str(result_file),
                    cwd=self.root,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async with asyncio.timeout(1800):
                    code = await process.wait()
                if code:
                    raise ValueError("TOOLSET_BUILD_FAILED")
                result = json.loads(result_file.read_text())
                if (
                    not isinstance(result["id"], str)
                    or not result["id"].startswith("updated-")
                    or not result["id"].removeprefix("updated-").isalnum()
                ):
                    raise ValueError("TOOLSET_RESULT_INVALID")
                path = self.root / "runtime/toolsets" / result["id"] / "manifest.json"
                manifest = json.loads(path.read_text())
                await self.register(manifest)
                await self.activate(manifest["id"])
                self.status[identity] = {"state": "completed", "toolset_id": manifest["id"]}
            except (Exception, asyncio.CancelledError) as exc:
                if process and process.returncode is None:
                    import psutil

                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                    await process.wait()
                self.status[identity] = {"state": "failed", "reason": type(exc).__name__}
            finally:
                result_file.unlink(missing_ok=True)

        self.update_task = asyncio.create_task(build(), name="toolset-update")
        return {"id": identity, "state": "running"}

    async def close(self):
        if self.update_task and not self.update_task.done():
            self.update_task.cancel()
            await self.update_task

    async def register(self, manifest: dict):
        for key in ("id", "python", "ffmpeg", "ffprobe", "node", "hashes", "versions", "contracts"):
            if key not in manifest:
                raise ValueError("TOOLSET_MANIFEST_INVALID")
        if manifest["contracts"] != 1:
            raise ValueError("TOOLSET_CONTRACT_UNVERIFIED")
        for key in ("python", "ffmpeg", "ffprobe", "node"):
            path = contained(self.root, Path(manifest[key]))
            expected = manifest["hashes"].get(manifest[key])
            if not expected:
                raise ValueError("TOOLSET_HASH_MISSING")

            def verify(p=path, value=expected):
                with p.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != value:
                        raise ValueError("TOOLSET_HASH_MISMATCH")

            await asyncio.to_thread(verify)
        await self.store.write(
            lambda c: c.execute(
                "INSERT INTO toolsets(id,manifest,active) VALUES(?,?,0) ON CONFLICT(id) DO NOTHING",
                (manifest["id"], json.dumps(manifest)),
            )
        )
        existing = await self.store.read(
            "SELECT manifest FROM toolsets WHERE id=?", (manifest["id"],)
        )
        if json.loads(existing[0]["manifest"]) != manifest:
            raise ValueError("TOOLSET_IMMUTABLE")
        await self.store.write(
            lambda c: c.execute(
                "INSERT INTO toolset_validation VALUES(?,1) "
                "ON CONFLICT(id) DO UPDATE SET verified=1",
                (manifest["id"],),
            )
        )
        return manifest["id"]

    async def activate(self, identity):
        rows = await self.store.read("SELECT manifest FROM toolsets WHERE id=?", (identity,))
        if not rows:
            raise ValueError("TOOLSET_NOT_FOUND")
        await self.register(json.loads(rows[0]["manifest"]))

        def switch(conn):
            conn.execute("UPDATE toolsets SET active=0 WHERE active=1")
            conn.execute("UPDATE toolsets SET active=1 WHERE id=?", (identity,))

        await self.store.write(switch)

    async def discover(self):
        await self.store.write(lambda c: c.execute("UPDATE toolset_validation SET verified=0"))
        for path in (self.root / "runtime/toolsets").glob("*/manifest.json"):
            try:
                await self.register(json.loads(path.read_text()))
            except (ValueError, OSError) as exc:
                await self.store.write(
                    lambda c, identity=path.parent.name: c.execute(
                        "UPDATE toolsets SET active=0 WHERE id=?", (identity,)
                    )
                )
                self.status[path.parent.name] = {"state": "failed", "reason": type(exc).__name__}
        # DBだけを移した場合も、ディスクに存在しない旧版を新規受付に使わない。
        await self.store.write(
            lambda c: c.execute(
                "UPDATE toolsets SET active=0 WHERE active=1 AND id NOT IN "
                "(SELECT id FROM toolset_validation WHERE verified=1)"
            )
        )
        if not await self.store.read("SELECT id FROM toolsets WHERE active=1"):
            rows = await self.store.read(
                "SELECT t.id FROM toolsets t JOIN toolset_validation v ON v.id=t.id "
                "WHERE v.verified=1 ORDER BY t.id DESC LIMIT 1"
            )
            if rows:
                await self.activate(rows[0]["id"])

    async def diagnostics(self):
        rows = await self.store.read("SELECT id,manifest,active FROM toolsets ORDER BY id DESC")
        return {
            "sqlite": sqlite3.sqlite_version,
            "tools": [
                {
                    "id": r["id"],
                    "active": bool(r["active"]),
                    "versions": json.loads(r["manifest"])["versions"],
                }
                for r in rows
            ],
            "date_filters_available": False,
            "contract_version": 1,
            "updates": self.status,
        }
