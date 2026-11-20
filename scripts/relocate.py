"""移設で無効になったtoolsetのvenvを保存済みハッシュ付きrequirementsから再構築する。"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = os.environ.get("DT_OFFLINE") == "1"
UV_OFFLINE = ["--offline"] if OFFLINE else []


def main():
    base_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    with base_python.open("rb") as stream:
        base_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    uv = ROOT / "runtime" / ("uv.exe" if os.name == "nt" else "uv")
    for path in (ROOT / "runtime/toolsets").glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        if manifest.get("base_python_sha256") != base_hash:
            raise RuntimeError("移設元と同じPython本体を確認できません: " + manifest["id"])
        requirements = path.parent / "requirements.txt"
        if not requirements.exists() or hashlib.sha256(
            requirements.read_bytes()
        ).hexdigest() != manifest.get("requirements_sha256"):
            raise RuntimeError("再構築用requirementsを検証できません: " + manifest["id"])
        environment = path.parent / "python"
        if environment.exists():
            shutil.rmtree(environment)
        base = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
        subprocess.run(
            [str(uv), "venv", "--python", str(base), str(environment)], check=True, timeout=60
        )
        python = ROOT / manifest["python"]
        subprocess.run(
            [
                str(uv),
                "pip",
                "sync",
                *UV_OFFLINE,
                "--python",
                str(python),
                "--require-hashes",
                str(requirements),
            ],
            check=True,
            timeout=300,
        )
        subprocess.run(
            [str(python), "-I", str(ROOT / "scripts/contract_probe.py")], check=True, timeout=90
        )
        # uvのWindows起動stubは配置パスを含むため、移設でハッシュが変わる。
        previous = json.loads(json.dumps(manifest))
        with python.open("rb") as stream:
            manifest["hashes"][manifest["python"]] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
        database = ROOT / "data/app.sqlite3"
        if database.exists():
            backups = ROOT / "data/backups"
            backups.mkdir(exist_ok=True)
            with closing(sqlite3.connect(database)) as connection:
                with closing(
                    sqlite3.connect(
                        backups / f"before-relocate-{int(time.time())}-{manifest['id']}.sqlite3"
                    )
                ) as backup:
                    connection.backup(backup)
                existing = connection.execute(
                    "SELECT manifest FROM toolsets WHERE id=?", (manifest["id"],)
                ).fetchone()
                if existing and json.loads(existing[0]) not in (previous, manifest):
                    raise RuntimeError("DBとtoolsetマニフェストが一致しません")
                connection.execute(
                    "UPDATE toolsets SET manifest=? WHERE id=?",
                    (json.dumps(manifest), manifest["id"]),
                )
                connection.commit()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    print("toolsetの参照IDと版を維持して再構築しました。")


if __name__ == "__main__":
    main()
