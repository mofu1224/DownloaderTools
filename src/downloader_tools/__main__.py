import argparse
import asyncio
import socket
import webbrowser
from pathlib import Path

import portalocker
import uvicorn

from .paths import atomic_json, private_directory, root_path


def main():
    parser = argparse.ArgumentParser(description="DownloaderTools ポータブルローカルサーバー")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--restore", type=Path, help="停止中のDBを整合性検査後に復元")
    args = parser.parse_args()
    root = root_path()
    private_directory(root / "data/instance")
    if args.restore:
        restore(root, args.restore)
        return
    lock = portalocker.Lock(str(root / "data/instance/app.lock"), timeout=0)
    try:
        lock.acquire()
    except portalocker.LockException:
        info = root / "data/instance/launch.json"
        if not args.no_browser and info.exists():
            import json

            webbrowser.open(json.loads(info.read_text())["url"])
        print("DownloaderToolsは既に起動しています。")
        return
    lock.release()
    listener = None
    for port in range(8765, 8785):
        candidate = socket.socket()
        try:
            candidate.bind(("127.0.0.1", port))
            candidate.listen(128)
            listener = candidate
            break
        except OSError:
            candidate.close()
    if listener is None:
        raise SystemExit("ポート8765〜8784が使用されています。")
    from .web import create_app

    app = create_app(root)
    url = f"http://127.0.0.1:{port}/#token={app.state.bootstrap_token}"
    # トークンは非公開のローカル起動情報だけへ記録し、アクセスログへ流さない。
    atomic_json(root / "data/instance/launch.json", {"url": url})
    if not args.no_browser:
        import threading

        threading.Timer(1, lambda: webbrowser.open(url)).start()
    print(f"DownloaderTools: http://127.0.0.1:{port}/  終了: Ctrl+C")
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, workers=1, access_log=False, timeout_graceful_shutdown=20
    )
    try:
        asyncio.run(uvicorn.Server(config).serve(sockets=[listener]))
    finally:
        listener.close()
        (root / "data/instance/launch.json").unlink(missing_ok=True)


def restore(root: Path, backup: Path):
    import os
    import sqlite3
    import tempfile
    from contextlib import closing

    from .domain import Profile, Settings
    from .store import uid

    path = root / "data/app.sqlite3"
    if path.exists() and backup.exists() and path.samefile(backup):
        raise SystemExit("復元元と現在のDBが同じです。別のバックアップを指定してください。")
    with portalocker.Lock(str(root / "data/instance/app.lock"), timeout=0):
        descriptor, name = tempfile.mkstemp(prefix="restore-", suffix=".sqlite3", dir=path.parent)
        os.close(descriptor)
        staged = Path(name)
        try:
            with (
                closing(
                    sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)
                ) as source,
                closing(sqlite3.connect(staged)) as target,
                closing(sqlite3.connect(":memory:")) as expected,
            ):
                source.backup(target)
                target.execute("PRAGMA trusted_schema=OFF")
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("integrity")
                version = target.execute("PRAGMA user_version").fetchone()[0]
                if version not in (1, 2):
                    raise ValueError("version")
                expected.executescript(Path(__file__).with_name("schema.sql").read_text())
                for (table,) in expected.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ):
                    if version == 1 and table == "policy_skips":
                        continue
                    columns = {r[1] for r in expected.execute(f'PRAGMA table_info("{table}")')}
                    actual = {r[1] for r in target.execute(f'PRAGMA table_info("{table}")')}
                    if not columns.issubset(actual):
                        raise ValueError("schema")
                if target.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("references")
                import json

                row = target.execute("SELECT body FROM settings WHERE id=1").fetchone()
                Settings.model_validate(json.loads(row[0] if row else "null"))
                for (body,) in target.execute("SELECT body FROM profiles"):
                    Profile.model_validate(json.loads(body))
                target.execute(
                    "UPDATE sources SET state='blocked',reason='restored',revision=revision+1 "
                    "WHERE state NOT IN ('succeeded','partial','failed','cancelled')"
                )
                target.commit()
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                target.execute("PRAGMA journal_mode=DELETE")
            if path.exists():
                (root / "data/backups").mkdir(exist_ok=True)
                with (
                    closing(sqlite3.connect(path)) as previous,
                    closing(
                        sqlite3.connect(root / "data/backups" / f"before-restore-{uid()}.sqlite3")
                    ) as saved,
                ):
                    previous.backup(saved)
                    if previous.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                        raise ValueError("busy")
            with staged.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(staged, path)
        except sqlite3.Error, ValueError, OSError:
            raise SystemExit(
                "バックアップを復元できません。形式・空き容量・アクセス権を確認してください。"
                "現在のDBは置換していません。"
            ) from None
        finally:
            staged.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(staged) + suffix).unlink(missing_ok=True)
    print("復元しました。未完了Sourceは保留しています。Cookieと保存先を再確認してください。")


if __name__ == "__main__":
    main()
