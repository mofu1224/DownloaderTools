"""公式配布物の検証とプロジェクト内への展開。アプリ起動前に実行する。

ネットワークが使えるときは公式の検証値を取得し、オフラインまたは取得失敗時は
`runtime/distributions.json` に記録済みの検証値へフォールバックする。取得した
アーカイブは `.cache/distributions` に保持し、再実行しても同じ内容を再利用する。
`--offline` を付けると一切の通信を行わない。
"""

import hashlib
import io
import json
import platform
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = "--offline" in sys.argv


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "DownloaderTools/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def recorded_digest(url, algorithm):
    """過去に検証済みの配布物ダイジェストを記録から読む。"""
    path = ROOT / "runtime/distributions.json"
    if not path.exists():
        return None
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    for record in reversed(records):
        if record.get("url") == url and record.get("algorithm") == algorithm:
            return record.get("digest")
    return None


def expected_digest(url, algorithm, live):
    """公式の検証値を得る。オフラインや取得失敗時は記録済みの値を使う。"""
    pinned = json.loads((ROOT / "scripts/runtime-lock.json").read_text(encoding="utf-8"))
    for record in pinned:
        if record["url"] == url and record["algorithm"] == algorithm:
            return record["digest"]
    if not OFFLINE:
        try:
            return live()
        except OSError, StopIteration:
            print("検証値を取得できないため、記録済みの値と照合します: " + url, flush=True)
    value = recorded_digest(url, algorithm)
    if not value:
        raise RuntimeError("配布物の検証値を取得できません（オフライン）: " + url)
    return value


def verified_zip(url, checksum, destination, algorithm="sha256", member=None, download_url=None):
    cache = ROOT / ".cache/distributions" / (hashlib.sha256(url.encode()).hexdigest() + ".zip")
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        body = cache.read_bytes()
    else:
        if OFFLINE:
            raise RuntimeError("オフラインでは配布物を取得できません: " + url)
        print("取得: " + (download_url or url), flush=True)
        body = fetch(download_url or url)
    if hashlib.new(algorithm, body).hexdigest() != checksum.lower():
        raise RuntimeError("配布物のハッシュが一致しません: " + url)
    cache.write_bytes(body)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for entry in archive.infolist():
            if member and not entry.filename.endswith(member):
                continue
            target = destination / (Path(entry.filename).name if member else entry.filename)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("不正なアーカイブパス")
            if not entry.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    return {
        "url": url,
        "algorithm": algorithm,
        "digest": checksum,
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def main():
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit(
            "この自動配布セットはWindows x64用です。macOSはREADMEの手順を参照してください。"
        )
    records = []
    python_root = Path(sys.base_prefix)
    if "--media-only" not in sys.argv:
        records.append(
            verified_zip(
                "https://sqlite.org/2026/sqlite-dll-win-x64-3540100.zip",
                "c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4",
                python_root / "DLLs",
                "sha3_256",
                "sqlite3.dll",
            )
        )
    node_name = "node-v24.22.3-win-x64.zip"
    node_url = "https://nodejs.org/dist/v24.22.3/" + node_name

    def node_checksum():
        sums = fetch("https://nodejs.org/dist/v24.22.3/SHASUMS256.txt").decode()
        return next(line.split()[0] for line in sums.splitlines() if line.endswith(node_name))

    if "--media-only" not in sys.argv:
        records.append(
            verified_zip(
                node_url,
                expected_digest(node_url, "sha256", node_checksum),
                ROOT / "runtime/build-tools",
            )
        )
    ffmpeg_url = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.1-essentials_build.zip"

    def ffmpeg_checksum():
        return fetch(ffmpeg_url + ".sha256").decode().split()[0]

    ffmpeg_sum = expected_digest(ffmpeg_url, "sha256", ffmpeg_checksum)
    # URL由来の既存キャッシュキーを維持し、新規取得だけ配布者のGitHub Releaseを使う。
    release = (
        "https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.zip"
    )
    for binary in ("ffmpeg.exe", "ffprobe.exe"):
        records.append(
            verified_zip(
                ffmpeg_url, ffmpeg_sum, ROOT / "runtime/media", member=binary, download_url=release
            )
        )
    record_path = ROOT / "runtime/distributions.json"
    old = json.loads(record_path.read_text()) if record_path.exists() else []
    merged = []
    for record in old + records:
        if not any(
            item.get("url") == record["url"] and item.get("digest") == record["digest"]
            for item in merged
        ):
            merged.append(record)
    record_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print("検証済みSQLite / Node / FFmpeg / FFprobeを配置しました。")


if __name__ == "__main__":
    main()
