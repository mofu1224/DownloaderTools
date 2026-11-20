import asyncio
import codecs
import json
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import portalocker
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import Field

from .credentials import Credentials
from .database_driver import sqlite3
from .domain import BatchRequest, Model, Profile, Settings
from .events import Events
from .paths import private_directory, root_path
from .scheduler import Scheduler
from .store import Store, uid
from .toolsets import Toolsets


class CookieRequest(Model):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(max_length=262144)


class NameRequest(Model):
    name: str = Field(min_length=1, max_length=80)


class RootRequest(Model):
    path: str = Field(min_length=1, max_length=200)


class RetryRequest(Model):
    source_id: str
    credential_id: str | None = None
    toolset_id: str | None = None


class OpenRequest(Model):
    output_root_id: str = "default"


class ToolRequest(Model):
    toolset_id: str | None = None


class SessionRequest(Model):
    token: str


def public_source(source, progress=None):
    allowed = (
        "id",
        "batch_id",
        "created_at",
        "provider",
        "kind",
        "state",
        "desired_state",
        "reason",
        "revision",
        "attempt_id",
        "completed_count",
        "skipped_count",
        "counts_complete",
        "phase",
        "profile_id",
        "credential_id",
        "toolset_id",
        "output_root_id",
        "elapsed",
        "time_budget",
        "cursor",
    )
    result = {key: source[key] for key in allowed if key in source}
    result["display_url"] = (urlsplit(source["url"]).hostname or "") + "/…"
    result["progress"] = progress or json.loads(source.get("progress", "{}"))
    return result


def create_app(root: Path | None = None, bootstrap_token: str | None = None, start_scheduler=True):
    root = (root or root_path()).resolve()
    store = Store(root / "data/app.sqlite3")
    events = Events()
    scheduler = Scheduler(store, root, events)
    credentials = Credentials(store, root)
    tools = Toolsets(store, root)
    token = bootstrap_token or secrets.token_urlsafe(32)
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    bootstrapped = False

    @asynccontextmanager
    async def lifespan(app):
        private_directory(root / "data")
        private_directory(root / "data/instance")
        (root / "downloads").mkdir(exist_ok=True)
        lock = portalocker.Lock(str(root / "data/instance/app.lock"), timeout=0)
        await asyncio.to_thread(lock.acquire)
        try:
            await store.start()
            await tools.discover()
            if start_scheduler:
                await scheduler.start()
            yield
        finally:
            await tools.close()
            if start_scheduler:
                await scheduler.close()
            await store.close()
            lock.release()

    app = FastAPI(
        title="DownloaderTools", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.bootstrap_token = token
    app.state.store = store
    app.state.scheduler = scheduler

    def error(code, status=422):
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": code,
                    "field": None,
                    "retryable": status == 503,
                    "request_id": uid(),
                }
            },
            status_code=status,
        )

    @app.middleware("http")
    async def protection(request, call_next):
        host = request.headers.get("host", "")
        try:
            parts = urlsplit("http://" + host)
            valid_host = parts.hostname == "127.0.0.1" and (
                not parts.port or 8765 <= parts.port <= 8784
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            return error("HOST_REJECTED", 400)
        origin = request.headers.get("origin")
        if origin and origin != "http://" + host:
            return error("ORIGIN_REJECTED", 403)
        if request.method not in ("GET", "HEAD"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    return error("BODY_TOO_LARGE", 413)
            request._body = bytes(body)
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/v1/session", "/api/v1/health"):
            if not secrets.compare_digest(
                request.cookies.get("dt_session", "").encode(), session.encode()
            ):
                return error("SESSION_REQUIRED", 401)
            if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), csrf.encode()
            ):
                return error("CSRF_REQUIRED", 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        code = str(exc)
        if not code or len(code) > 100 or not code.replace("_", "").isalnum():
            code = "INPUT_INVALID"
        status = (
            409
            if any(
                word in code for word in ("CONFLICT", "IN_USE", "TERMINAL", "REQUIRED", "IMMUTABLE")
            )
            else 422
        )
        if code in ("QUEUE_FULL", "DB_QUEUE_FULL"):
            status = 503
        return error(code, status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error("INPUT_INVALID")

    @app.exception_handler(sqlite3.Error)
    async def db_error(request, exc):
        scheduler.wake.set()
        return error("DB_UNAVAILABLE", 503)

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "degraded" if store.fault or scheduler.error else "ok",
            "version": "0.1.0",
        }

    @app.post("/api/v1/session")
    async def establish(body: SessionRequest):
        nonlocal bootstrapped
        if bootstrapped or not secrets.compare_digest(body.token.encode(), token.encode()):
            return error("BOOTSTRAP_INVALID", 401)
        bootstrapped = True
        response = JSONResponse({"csrf": csrf})
        response.set_cookie("dt_session", session, httponly=True, samesite="strict")
        return response

    @app.get("/api/v1/session")
    async def current_session(request: Request):
        if not secrets.compare_digest(
            request.cookies.get("dt_session", "").encode(), session.encode()
        ):
            return error("SESSION_REQUIRED", 401)
        return {"csrf": csrf}

    async def snapshot():
        sources = await store.sources(active=True, limit=1000)
        return {
            "sources": [public_source(s, scheduler.progress.get(s["id"])) for s in sources],
            "diagnostics": {
                "error": scheduler.error or store.fault,
                "running": len(scheduler.active),
            },
        }

    @app.get("/api/v1/snapshot")
    async def get_snapshot():
        return await snapshot()

    @app.post("/api/v1/batches", status_code=202)
    async def create_batch(body: BatchRequest, idempotency_key: str = Header()):
        result = await store.create_batch(body.model_dump(), idempotency_key)
        scheduler.wake.set()
        events.publish({"type": "refresh"})
        return result

    @app.get("/api/v1/batches")
    async def list_batches(cursor: int | None = None, limit: int = 50):
        rows = await store.read(
            "SELECT rowid AS cursor,id,kind,created_at,response FROM batches "
            "WHERE rowid<? ORDER BY rowid DESC LIMIT ?",
            (cursor or 2**63 - 1, min(max(limit, 1), 200)),
        )
        return {
            "items": [{**r, "response": json.loads(r["response"])} for r in rows],
            "next_cursor": rows[-1]["cursor"] if len(rows) == min(max(limit, 1), 200) else None,
        }

    @app.get("/api/v1/batches/{identity}")
    async def batch_detail(identity: str):
        sources = await store.read(
            "SELECT s.*,b.shared FROM batch_sources b JOIN sources s ON s.id=b.source_id "
            "WHERE b.batch_id=? ORDER BY b.ordinal",
            (identity,),
        )
        return {
            "id": identity,
            "sources": [{**public_source(s), "shared": bool(s["shared"])} for s in sources],
        }

    @app.get("/api/v1/sources")
    async def source_history(cursor: int | None = None, limit: int = 50):
        rows = await store.sources(cursor=cursor, limit=min(max(limit, 1), 200))
        return {
            "items": [public_source(s) for s in rows],
            "next_cursor": rows[-1]["cursor"] if rows else None,
        }

    @app.get("/api/v1/sources/{identity}")
    async def source_detail(identity: str):
        source = await store.source(identity)
        await scheduler.publisher.check_catalog(identity)
        return {
            **public_source(source),
            "artifacts": await store.artifacts(identity),
            "skip_reasons": await store.read(
                "SELECT reason,count(*) AS count FROM policy_skips WHERE source_id=? GROUP BY reason",
                (identity,),
            ),
            "profile": json.loads(source["profile"]),
            "references": (
                await store.read(
                    "SELECT count(*) AS n FROM batch_sources WHERE source_id=?", (identity,)
                )
            )[0]["n"],
        }

    @app.post("/api/v1/sources/{identity}/{action}")
    async def control(identity: str, action: str, budget: int | None = None):
        if budget is not None and not 60 <= budget <= 604800:
            raise ValueError("TIME_BUDGET_INVALID")
        if action == "resume":
            source = await store.source(identity)
            if source["output_root_id"] in scheduler.blocked_roots:
                await scheduler.resolve_quarantine(source["output_root_id"])
        result = await store.control(identity, action, budget)
        scheduler.wake.set()
        events.publish({"type": "refresh"})
        return public_source(result)

    @app.post("/api/v1/retries", status_code=202)
    async def retry(body: RetryRequest, idempotency_key: str = Header()):
        source = await store.source(body.source_id)
        request = {
            "kind": source["kind"],
            "urls": [source["url"]],
            "profile_id": source["profile_id"],
            "credential_id": body.credential_id or source["credential_id"],
            "output_root_id": source["output_root_id"],
        }
        if body.toolset_id:
            active = await store.read("SELECT id FROM toolsets WHERE active=1")
            if not active or active[0]["id"] != body.toolset_id:
                raise ValueError("SELECT_ACTIVE_TOOLSET")
        result = await store.create_batch(request, idempotency_key)
        scheduler.wake.set()
        events.publish({"type": "refresh"})
        return result

    @app.get("/api/v1/sources/{identity}/logs")
    async def logs(identity: str, offset: int = 0):
        await store.source(identity)
        path = root / "data/logs" / (identity + ".log")

        def read_log():
            if not path.exists():
                return {"text": "", "next_offset": 0}
            with path.open("rb") as stream:
                stream.seek(max(0, offset))
                text = stream.read(65536)
                # 増分読込の境界にあるUTF-8文字は次の応答へ残す。
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                decoded = decoder.decode(text)
                pending, _ = decoder.getstate()
                return {
                    "text": decoded,
                    "next_offset": stream.tell() - len(pending),
                }

        return await asyncio.to_thread(read_log)

    @app.get("/api/v1/events")
    async def stream_events(request: Request):
        async def generate():
            async with events.subscribe() as queue:
                yield "event: snapshot\ndata: " + json.dumps(await snapshot()) + "\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                        if event is None:
                            return
                        yield "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                    except TimeoutError:
                        yield ": heartbeat\n\n"

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/v1/profiles")
    async def profiles():
        return await store.profiles()

    @app.post("/api/v1/profiles", status_code=201)
    async def add_profile(body: Profile):
        return await store.add_profile(body.model_dump(mode="json"))

    @app.get("/api/v1/settings")
    async def settings():
        return await store.settings()

    @app.patch("/api/v1/settings")
    async def save_settings(body: Settings):
        result = await store.set_settings(body.model_dump())
        scheduler.wake.set()
        return result

    @app.get("/api/v1/credentials")
    async def list_credentials():
        return await credentials.list()

    @app.post("/api/v1/credentials", status_code=201)
    async def add_credential(body: CookieRequest):
        return await credentials.create(body.name, body.content)

    @app.patch("/api/v1/credentials/{identity}")
    async def rename_credential(identity: str, body: NameRequest):
        await store.write(
            lambda c: c.execute("UPDATE credentials SET name=? WHERE id=?", (body.name, identity))
        )
        return {"id": identity, "name": body.name}

    @app.delete("/api/v1/credentials/{identity}")
    async def delete_credential(identity: str):
        await credentials.delete(identity)
        return {"deleted": True}

    @app.get("/api/v1/output-roots")
    async def roots():
        return await store.roots()

    @app.post("/api/v1/output-roots", status_code=201)
    async def add_root(body: RootRequest):
        path = Path(body.path)
        if not path.is_absolute():
            path = root / path
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise ValueError("PATH_LINK_FORBIDDEN")
        identity = uid()
        await store.register_root(identity, path.resolve())
        try:
            await scheduler.lock_root(identity)
        except OSError, ValueError, portalocker.LockException:
            await store.write(
                lambda c: c.execute("DELETE FROM output_roots WHERE id=?", (identity,))
            )
            raise ValueError("OUTPUT_UNAVAILABLE") from None
        return {"id": identity, "path": str(path), "verified": True}

    @app.post("/api/v1/outputs/open")
    async def open_output(body: OpenRequest):
        path = await scheduler.publisher.root(body.output_root_id)
        if not path.exists():
            raise ValueError("OUTPUT_NOT_FOUND")
        if sys.platform == "win32":
            await asyncio.to_thread(os.startfile, path)
        elif sys.platform == "darwin":
            await asyncio.create_subprocess_exec("/usr/bin/open", str(path))
        return {"opened": True}

    @app.get("/api/v1/diagnostics")
    async def diagnostics():
        return {
            **await tools.diagnostics(),
            "scheduler_error": scheduler.error,
            "db_error": store.fault,
            "blocked_roots": list(scheduler.blocked_roots),
            "date_filters_available": False,
        }

    @app.post("/api/v1/toolset-updates", status_code=202)
    async def switch_tools(body: ToolRequest):
        if not body.toolset_id:
            return await tools.update()
        identity = uid()
        tools.status[identity] = {"state": "verifying", "toolset_id": body.toolset_id}
        try:
            await tools.activate(body.toolset_id)
        except ValueError, OSError:
            tools.status[identity]["state"] = "failed"
            raise
        tools.status[identity]["state"] = "completed"
        scheduler.wake.set()
        return {"id": identity, **tools.status[identity]}

    @app.get("/api/v1/toolset-updates/{identity}")
    async def update_status(identity: str):
        if identity not in tools.status:
            raise ValueError("UPDATE_NOT_FOUND")
        return tools.status[identity]

    @app.post("/api/v1/backups", status_code=201)
    async def backup():
        identity = str(int(time.time())) + "-" + uid()[:8]
        await store.backup(root / "data/backups" / (identity + ".sqlite3"))
        return {"id": identity, "contains": "設定・履歴・元URL（Cookie本文とメディアは含みません）"}

    templates = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape()
    )
    static = Path(__file__).parent / "static"
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return templates.get_template("index.html").render()

    return app
