import asyncio
import json
import runpy
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from downloader_tools.__main__ import restore
from downloader_tools.paths import root_path
from downloader_tools.store import Store
from downloader_tools.web import create_app


def prepare_database(root):
    async def create():
        store = Store(root / "data/app.sqlite3")
        await store.start()
        try:
            batch = await store.create_batch(
                {
                    "kind": "gallery",
                    "urls": ["https://example.com/fixture"],
                    "profile_id": "gallery-default",
                },
                "backup-fixture",
            )
            return batch["sources"][0]["id"]
        finally:
            await store.close()

    (root / "data/instance").mkdir(parents=True)
    return asyncio.run(create())


def test_restore_rejects_wrong_schema_without_replacing_existing_data(tmp_path):
    prepare_database(tmp_path)
    database = tmp_path / "data/app.sqlite3"
    before = database.read_bytes()
    wrong = tmp_path / "wrong.sqlite3"
    with closing(sqlite3.connect(wrong)) as conn:
        conn.execute("PRAGMA user_version=2")
        conn.execute("CREATE TABLE unrelated(value TEXT)")
        conn.commit()
    with pytest.raises(SystemExit, match="バックアップ"):
        restore(tmp_path, wrong)
    assert database.read_bytes() == before


def test_restore_preserves_backup_and_holds_unfinished_sources(tmp_path):
    source_id = prepare_database(tmp_path)
    backup = tmp_path / "snapshot.sqlite3"
    database = tmp_path / "data/app.sqlite3"
    with closing(sqlite3.connect(database)) as conn, closing(sqlite3.connect(backup)) as target:
        conn.backup(target)
        conn.execute("UPDATE sources SET completed_count=7 WHERE id=?", (source_id,))
        conn.commit()
    before_backup = backup.read_bytes()
    restore(tmp_path, backup)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute(
            "SELECT state,reason,completed_count FROM sources WHERE id=?", (source_id,)
        ).fetchone() == ("blocked", "restored", 0)
    saved = list((tmp_path / "data/backups").glob("before-restore-*.sqlite3"))
    assert len(saved) == 1
    with closing(sqlite3.connect(saved[0])) as conn:
        assert conn.execute("SELECT completed_count FROM sources").fetchone()[0] == 7
    assert backup.read_bytes() == before_backup


def test_restore_rejects_live_database_as_its_own_backup(tmp_path):
    prepare_database(tmp_path)
    with pytest.raises(SystemExit, match="同じ"):
        restore(tmp_path, tmp_path / "data/app.sqlite3")


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_restore_write_failure_preserves_current_data_and_cleans_staging(
    tmp_path, monkeypatch, operation
):
    import errno
    import os

    prepare_database(tmp_path)
    database = tmp_path / "data/app.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    backup.write_bytes(database.read_bytes())
    before = database.read_bytes()

    def denied(*args):
        if operation == "fsync":
            raise OSError(errno.ENOSPC, "fixture: disk full")
        raise PermissionError("fixture: replacement denied")

    monkeypatch.setattr(os, operation, denied)
    with pytest.raises(SystemExit, match="置換していません"):
        restore(tmp_path, backup)
    assert database.read_bytes() == before
    assert not list(database.parent.glob("restore-*"))


@pytest.mark.parametrize("version", [1, 999])
def test_restore_schema_versions(tmp_path, version):
    prepare_database(tmp_path)
    database = tmp_path / "data/app.sqlite3"
    backup = tmp_path / "versioned.sqlite3"
    with closing(sqlite3.connect(database)) as conn, closing(sqlite3.connect(backup)) as target:
        conn.backup(target)
        if version == 1:
            target.execute("DROP TABLE policy_skips")
        target.execute(f"PRAGMA user_version={version}")
        target.commit()
    before = database.read_bytes()
    if version == 999:
        with pytest.raises(SystemExit, match="バックアップ"):
            restore(tmp_path, backup)
        assert database.read_bytes() == before
    else:
        restore(tmp_path, backup)

        async def reopen():
            store = Store(database)
            await store.start()
            try:
                assert len(await store.sources()) == 1
                assert (await store.sources())[0]["reason"] == "restored"
            finally:
                await store.close()

        asyncio.run(reopen())
        with closing(sqlite3.connect(database)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_portable_copy_does_not_share_writable_files_with_original(tmp_path):
    script = runpy.run_path(str(root_path() / "scripts/verify_portable.py"))
    original = tmp_path / "original"
    original.mkdir()
    (original / "manifest.json").write_text('{"value":1}')
    copied = tmp_path / "copy"
    script["copy_entry"](original, copied)
    (copied / "manifest.json").write_text('{"value":2}')
    assert json.loads((original / "manifest.json").read_text()) == {"value": 1}


def test_runtime_checksum_is_pinned_without_network():
    script = runpy.run_path(str(root_path() / "scripts/prepare_runtime.py"))

    def unexpected_network():
        pytest.fail("固定済み配布物の検証値をネットワークから再解決してはいけません")

    assert (
        script["expected_digest"](
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "sha256",
            unexpected_network,
        )
        == "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
    )


def test_clean_clone_copy_is_reproducible_and_excludes_local_state(tmp_path):
    script = runpy.run_path(str(root_path() / "scripts/verify_portable.py"))
    first = script["build_copy"](tmp_path / "first", source_only=True)
    second = script["build_copy"](tmp_path / "second", source_only=True)
    assert first == second
    assert (tmp_path / "first/README.md").read_bytes() == (root_path() / "README.md").read_bytes()
    names = first["files"]
    assert "src/downloader_tools/static/app.js" in names
    assert "scripts/runtime-lock.json" in names
    assert not any(
        name.startswith(("data/", "downloads/", "runtime/", ".cache/", ".venv/"))
        or "node_modules" in name
        or "__pycache__" in name
        or name.endswith(".zip")
        or name == "scripts/package_source.py"
        for name in names
    )


def test_verification_runner_detects_failure_timeout_and_cleans_process(tmp_path):
    import os
    import sys

    import psutil

    script = runpy.run_path(str(root_path() / "scripts/verify.py"))
    # ログの相対パスは実際の検証用ディレクトリを基準にする。
    script["run_check"].__globals__["ROOT"] = tmp_path
    failure = script["run_check"](
        [sys.executable, "-c", "raise SystemExit(7)"], 5, tmp_path / "failure.log", os.environ
    )
    assert failure["exit_code"] == 7
    log = tmp_path / "timeout.log"
    timed_out = script["run_check"](
        [
            sys.executable,
            "-u",
            "-c",
            "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
        ],
        1,
        log,
        os.environ,
    )
    assert timed_out["exit_code"] == 124
    assert not psutil.pid_exists(int(log.read_text().strip()))


def test_user_document_local_links_exist():
    import re
    from urllib.parse import unquote

    for name in (
        "README.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        for link in re.findall(r"\]\(([^)]+)\)", (root_path() / name).read_text(encoding="utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            assert (root_path() / unquote(link.split("#", 1)[0])).is_file(), (name, link)


def test_non_ascii_auth_token_is_rejected_and_does_not_consume_bootstrap(tmp_path):
    with TestClient(
        create_app(tmp_path, bootstrap_token="fixture", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        invalid = client.post("/api/v1/session", json={"token": "不正なトークン"})
        assert invalid.status_code == 401
        assert "不正なトークン" not in invalid.text
        assert client.post("/api/v1/session", json={"token": "fixture"}).status_code == 200


def test_non_ascii_csrf_header_is_rejected_without_server_error(tmp_path):
    with TestClient(
        create_app(tmp_path, bootstrap_token="fixture", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        assert client.post("/api/v1/session", json={"token": "fixture"}).status_code == 200
        response = client.post("/api/v1/backups", headers={"X-CSRF-Token": b"\xff"})
        assert response.status_code == 403


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), "not-an-expiry"])
def test_invalid_cookie_expiry_has_stable_error_and_creates_no_secret_file(tmp_path, expiry):
    with TestClient(
        create_app(tmp_path, bootstrap_token="fixture", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        session = client.post("/api/v1/session", json={"token": "fixture"}).json()
        response = client.post(
            "/api/v1/credentials",
            headers={"X-CSRF-Token": session["csrf"]},
            json={
                "name": "fixture",
                "content": json.dumps(
                    [
                        {
                            "domain": ".example.com",
                            "name": "sid",
                            "value": "fixture-secret",
                            "expires": expiry,
                        }
                    ]
                ),
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "COOKIE_INVALID"
        assert "fixture-secret" not in response.text
        assert not list((tmp_path / "data/credentials").glob("*.txt"))
