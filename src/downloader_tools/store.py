import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from .database_driver import sqlite3
from .domain import TERMINAL, BatchRequest, Profile, Settings, digest, provider
from .gallery_dates import GALLERY_VERSION, supports_url


def uid() -> str:
    return uuid4().hex


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")
        self.pending = 0
        self.connection: sqlite3.Connection | None = None
        self.fault: str | None = None

    async def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def initialize():
            if sqlite3.sqlite_version_info < (3, 51, 3) and sqlite3.sqlite_version not in (
                "3.44.6",
                "3.50.7",
            ):
                raise RuntimeError("SQLITE_UPDATE_REQUIRED: " + sqlite3.sqlite_version)
            self.connection = sqlite3.connect(self.path, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("DB_SCHEMA_UNSUPPORTED")
            if version == 1:
                directory = self.path.parent / "backups"
                directory.mkdir(exist_ok=True)
                with closing(
                    sqlite3.connect(directory / f"schema-v1-{int(time.time())}.sqlite3")
                ) as backup:
                    self.connection.backup(backup)
            self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
            for key, expected in (("journal_mode", "wal"), ("synchronous", 2), ("foreign_keys", 1)):
                if self.connection.execute("PRAGMA " + key).fetchone()[0] != expected:
                    raise RuntimeError("DB_CONFIGURATION_INVALID")
            self.connection.execute(
                "INSERT OR IGNORE INTO settings VALUES(1,?)", (Settings().model_dump_json(),)
            )
            for kind in ("video", "gallery"):
                self.connection.execute(
                    "INSERT OR IGNORE INTO profiles VALUES(?,?)",
                    (kind + "-default", Profile(kind=kind).model_dump_json()),
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO output_roots VALUES('default',?,0)", ("downloads",)
            )

        await asyncio.get_running_loop().run_in_executor(self.pool, initialize)

    async def write(self, operation):
        if self.pending >= 256:
            raise ValueError("DB_QUEUE_FULL")
        self.pending += 1

        def transact():
            assert self.connection is not None
            conn = self.connection
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = operation(conn)
                conn.execute("COMMIT")
                return result
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        future = asyncio.get_running_loop().run_in_executor(self.pool, transact)

        def settled(result):
            self.pending -= 1
            exception = result.exception()
            if isinstance(exception, sqlite3.Error):
                self.fault = type(exception).__name__

        future.add_done_callback(settled)
        return await asyncio.shield(future)

    async def read(self, sql: str, args=()) -> list[dict[str, Any]]:
        def query():
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as conn:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(sql, args)]

        return await asyncio.to_thread(query)

    async def close(self):
        def shutdown():
            if self.connection:
                self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self.connection.close()

        await asyncio.get_running_loop().run_in_executor(self.pool, shutdown)
        self.pool.shutdown(wait=True)

    async def settings(self) -> dict:
        return json.loads((await self.read("SELECT body FROM settings WHERE id=1"))[0]["body"])

    async def set_settings(self, values: dict):
        candidate = Settings.model_validate(values)

        def update(conn):
            old = json.loads(conn.execute("SELECT body FROM settings WHERE id=1").fetchone()[0])
            if candidate.revision != old["revision"]:
                raise ValueError("REVISION_CONFLICT")
            updated = candidate.model_copy(update={"revision": candidate.revision + 1})
            conn.execute("UPDATE settings SET body=? WHERE id=1", (updated.model_dump_json(),))
            return updated.model_dump()

        return await self.write(update)

    async def register_root(self, root_id: str, path: Path):
        await self.write(
            lambda c: c.execute(
                "INSERT INTO output_roots VALUES(?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET path=excluded.path,verified=1",
                (root_id, str(path)),
            )
        )

    async def roots(self):
        return await self.read("SELECT * FROM output_roots ORDER BY id")

    async def add_profile(self, values: dict):
        profile = Profile.model_validate(values)
        profile_id = uid()
        await self.write(
            lambda c: c.execute(
                "INSERT INTO profiles VALUES(?,?)", (profile_id, profile.model_dump_json())
            )
        )
        return {"id": profile_id, **profile.model_dump(mode="json")}

    async def profiles(self):
        return [
            {"id": row["id"], **json.loads(row["body"])}
            for row in await self.read("SELECT * FROM profiles")
        ]

    async def create_batch(self, request: dict, key: str):
        values = BatchRequest.model_validate(request).model_dump()
        fingerprint = digest(values)
        if not key or len(key) > 128:
            raise ValueError("IDEMPOTENCY_KEY_INVALID")

        def create(conn):
            previous = conn.execute(
                "SELECT digest,response FROM batches WHERE idempotency_key=?", (key,)
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return json.loads(previous[1])
            row = conn.execute(
                "SELECT body FROM profiles WHERE id=?", (values["profile_id"],)
            ).fetchone()
            if row is None:
                raise ValueError("PROFILE_NOT_FOUND")
            profile = Profile.model_validate_json(row[0])
            if profile.kind != values["kind"]:
                raise ValueError("PROFILE_KIND_MISMATCH")
            if (
                conn.execute(
                    "SELECT 1 FROM output_roots WHERE id=?", (values["output_root_id"],)
                ).fetchone()
                is None
            ):
                raise ValueError("OUTPUT_NOT_FOUND")
            credential = values["credential_id"]
            if credential:
                entry = conn.execute(
                    "SELECT path FROM credentials WHERE id=? AND path!=''", (credential,)
                ).fetchone()
                if not entry:
                    raise ValueError("CREDENTIAL_REPLACEMENT_REQUIRED")
                if entry["path"].endswith(".pixiv-token") and (
                    profile.kind != "gallery"
                    or not all(supports_url(url) for url in values["urls"])
                ):
                    raise ValueError("PIXIV_CREDENTIAL_SCOPE")
            tool = conn.execute("SELECT id,manifest FROM toolsets WHERE active=1").fetchone()
            if (profile.date_from or profile.date_to) and (
                not all(supports_url(url) for url in values["urls"])
                or not tool
                or json.loads(tool["manifest"]).get("versions", {}).get("gallery-dl")
                != GALLERY_VERSION
            ):
                raise ValueError("DATE_CONTRACT_UNVERIFIED")
            tool_id = tool[0] if tool else "unavailable"
            batch_id = uid()
            conn.execute(
                "INSERT INTO batches VALUES(?,?,?,?,?,?)",
                (batch_id, time.time(), values["kind"], key, fingerprint, "{}"),
            )
            refs = []
            for ordinal, url in enumerate(values["urls"]):
                active = digest([url, row[0], credential, tool_id, values["output_root_id"]])
                existing = conn.execute(
                    "SELECT id FROM sources WHERE fingerprint=? AND state NOT IN "
                    "('succeeded','partial','failed','cancelled')",
                    (active,),
                ).fetchone()
                shared = existing is not None
                source_id = existing[0] if existing else uid()
                if not shared:
                    count = conn.execute(
                        "SELECT count(*) FROM sources WHERE state NOT IN "
                        "('succeeded','partial','failed','cancelled')"
                    ).fetchone()[0]
                    if count >= 1000:
                        raise ValueError("QUEUE_FULL")
                    conn.execute(
                        "INSERT INTO sources(id,batch_id,created_at,url,provider,kind,state,profile_id,"
                        "profile,credential_id,toolset_id,output_root_id,fingerprint,time_budget) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            source_id,
                            batch_id,
                            time.time(),
                            url,
                            provider(url),
                            values["kind"],
                            "queued",
                            values["profile_id"],
                            row[0],
                            credential,
                            tool_id,
                            values["output_root_id"],
                            active,
                            json.loads(
                                conn.execute("SELECT body FROM settings WHERE id=1").fetchone()[0]
                            )["time_budget"],
                        ),
                    )
                conn.execute(
                    "INSERT INTO batch_sources VALUES(?,?,?,?)",
                    (batch_id, source_id, ordinal, int(shared)),
                )
                refs.append({"id": source_id, "shared": shared})
            response = {"id": batch_id, "sources": refs}
            conn.execute(
                "UPDATE batches SET response=? WHERE id=?", (json.dumps(response), batch_id)
            )
            return response

        return await self.write(create)

    async def sources(self, active=False, cursor: float | None = None, limit=200):
        where = "state NOT IN ('succeeded','partial','failed','cancelled')" if active else "1=1"
        args: list[Any] = []
        if cursor is not None:
            where += " AND rowid<?"
            args.append(int(cursor))
        args.append(min(limit, 1000 if active else 200))
        return await self.read(
            f"SELECT rowid AS cursor,* FROM sources WHERE {where} ORDER BY rowid DESC LIMIT ?", args
        )

    async def source(self, source_id):
        rows = await self.read("SELECT * FROM sources WHERE id=?", (source_id,))
        if not rows:
            raise ValueError("SOURCE_NOT_FOUND")
        return rows[0]

    async def claim(self, source_id):
        def claim_one(conn):
            row = conn.execute(
                "SELECT * FROM sources WHERE id=? AND state='queued' AND desired_state='run' "
                "AND next_eligible_at<=?",
                (source_id, time.time()),
            ).fetchone()
            if row is None:
                return None
            attempt_id, token = uid(), uid()
            ordinal = conn.execute(
                "SELECT count(*)+1 FROM attempts WHERE source_id=?", (source_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO attempts(id,source_id,ordinal,run_token,started_at) VALUES(?,?,?,?,?)",
                (attempt_id, source_id, ordinal, token, time.time()),
            )
            conn.execute(
                "UPDATE sources SET state='running',phase='starting',reason=NULL,attempt_id=?,run_token=?,"
                "revision=revision+1 WHERE id=?",
                (attempt_id, token, source_id),
            )
            return dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())

        return await self.write(claim_one)

    async def control(self, source_id, action, budget=None):
        if action not in ("pause", "resume", "cancel"):
            raise ValueError("ACTION_INVALID")

        def update(conn):
            source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise ValueError("SOURCE_NOT_FOUND")
            if source["state"] in TERMINAL:
                if action == "resume":
                    raise ValueError("SOURCE_TERMINAL")
                return dict(source)
            desired = {"pause": "pause", "resume": "run", "cancel": "cancel"}[action]
            live = source["state"] in ("running", "stopping", "recovering")
            state = "stopping" if live and action != "resume" else source["state"]
            if not live:
                state = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action]
                if action == "resume" and source["reason"] == "time_budget" and not budget:
                    raise ValueError("TIME_BUDGET_EXTENSION_REQUIRED")
            conn.execute(
                "UPDATE sources SET desired_state=?,state=?,reason=NULL,revision=revision+1,"
                "time_budget=? WHERE id=?",
                (desired, state, budget or source["time_budget"], source_id),
            )
            return dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())

        return await self.write(update)

    async def finish(self, source_id, token, exit_code, failures, counts_complete, reason=None):
        def finish_one(conn):
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND run_token=? "
                "AND state IN ('running','stopping','recovering')",
                (source_id, token),
            ).fetchone()
            if source is None:
                return False
            started = conn.execute(
                "SELECT started_at FROM attempts WHERE id=?", (source["attempt_id"],)
            ).fetchone()[0]
            spent = max(0, time.time() - started)
            retry_stall = (
                reason == "ENGINE_STALLED"
                and source["desired_state"] == "run"
                and source["crash_retries"] < 2
                and source["elapsed"] + spent < source["time_budget"]
            )
            if source["desired_state"] == "cancel":
                state = "cancelled"
            elif source["desired_state"] == "pause" or reason == "shutdown":
                state = "paused"
            elif reason == "restart_after_stop":
                state = "queued"
            elif retry_stall:
                state = "queued"
            elif reason:
                state = "blocked"
            elif exit_code == 0 and not failures and counts_complete:
                state = "succeeded"
            else:
                state = "partial" if source["completed_count"] else "failed"
            conn.execute(
                "UPDATE attempts SET ended_at=?,exit_code=?,result=? WHERE id=?",
                (time.time(), exit_code, state, source["attempt_id"]),
            )
            conn.execute(
                "UPDATE sources SET state=?,phase=?,reason=?,counts_complete=?,elapsed=elapsed+?,"
                "crash_retries=crash_retries+?,next_eligible_at=?,revision=revision+1 WHERE id=?",
                (
                    state,
                    state,
                    reason,
                    int(counts_complete),
                    spent,
                    int(retry_stall),
                    time.time() + 5 if retry_stall else 0,
                    source_id,
                ),
            )
            return True

        return await self.write(finish_one)

    async def block(self, source_id, reason):
        await self.write(
            lambda c: c.execute(
                "UPDATE sources SET state='blocked',reason=?,phase='blocked',"
                "revision=revision+1 WHERE id=? AND state NOT IN ('succeeded','partial','failed','cancelled')",
                (reason, source_id),
            )
        )

    async def artifacts(self, source_id):
        return await self.read(
            "SELECT * FROM artifacts WHERE source_id=? ORDER BY checked_at DESC LIMIT 200",
            (source_id,),
        )

    async def backup(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)

        def copy():
            assert self.connection
            with sqlite3.connect(path) as output:
                self.connection.backup(output)

        await asyncio.get_running_loop().run_in_executor(self.pool, copy)
        return path
