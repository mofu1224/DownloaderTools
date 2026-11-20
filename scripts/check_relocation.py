"""別パスへ移設したコピーで、venv再構築・契約検証・起動を確認する。"""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def fixture_database(destination):
    from downloader_tools.paths import private_directory
    from downloader_tools.store import Store

    private_directory(destination / "data")
    store = Store(destination / "data/app.sqlite3")
    await store.start()
    try:
        for index, path in enumerate((destination / "runtime/toolsets").glob("*/manifest.json")):
            manifest = json.loads(path.read_text())
            await store.write(
                lambda c, m=manifest, active=int(index == 0): c.execute(
                    "INSERT INTO toolsets VALUES(?,?,?)", (m["id"], json.dumps(m), active)
                )
            )
        batch = await store.create_batch(
            {
                "kind": "gallery",
                "urls": ["https://example.com/relocation-fixture"],
                "profile_id": "gallery-default",
            },
            "relocation-fixture",
        )
        await store.control(batch["sources"][0]["id"], "pause")
    finally:
        await store.close()


def main():
    destination = ROOT / ".cache/relocation-check"
    if destination.exists():
        raise SystemExit("前回の移設試験フォルダが残っています。内容を確認してください。")
    destination.mkdir(parents=True)
    try:
        for directory in ("src", "scripts", "frontend"):
            shutil.copytree(
                ROOT / directory,
                destination / directory,
                ignore=shutil.ignore_patterns("node_modules", "__pycache__"),
            )
        for filename in ("pyproject.toml", "uv.lock", ".python-version"):
            shutil.copyfile(ROOT / filename, destination / filename)
        shutil.copytree(
            ROOT / "runtime",
            destination / "runtime",
            copy_function=os.link,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        asyncio.run(fixture_database(destination))
        env = {
            **os.environ,
            "UV_CACHE_DIR": str(ROOT / ".cache/uv"),
            "UV_PYTHON_INSTALL_DIR": str(destination / "runtime/python"),
            "UV_PYTHON_BIN_DIR": str(destination / "runtime/bin"),
            "PYTHONUTF8": "1",
        }
        uv = destination / "runtime/uv.exe"
        subprocess.run(
            [str(uv), "sync", "--locked", "--all-groups"],
            cwd=destination,
            env=env,
            check=True,
            timeout=120,
        )
        python = destination / ".venv/Scripts/python.exe"
        subprocess.run(
            [str(python), "scripts/relocate.py"], cwd=destination, env=env, check=True, timeout=180
        )
        subprocess.run(
            [str(python), "scripts/smoke_app.py"], cwd=destination, env=env, check=True, timeout=60
        )
        print("移設先での環境再生成・固定版維持・起動: OK")
    finally:
        shutil.rmtree(destination)


if __name__ == "__main__":
    main()
