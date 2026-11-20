import functools
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from .domain import Profile
from .engines import build_command, parse_line
from .paths import root_path


def verify(manifest: dict):
    cache = root_path() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="contract-", dir=cache) as temporary:
        folder = Path(temporary)
        Image.new("RGB", (16, 16), "blue").save(folder / "sample.png")
        subprocess.run(
            [
                str(root_path() / manifest["ffmpeg"]),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:d=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(folder / "sample.mp4"),
            ],
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=folder))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for kind, extension in (("video", "mp4"), ("gallery", "png")):
                source = {
                    "id": "contract",
                    "kind": kind,
                    "profile": Profile.model_validate({"kind": kind}).model_dump_json(),
                    "url": f"http://127.0.0.1:{server.server_port}/sample.{extension}",
                }
                work = folder / kind
                command = build_command(source, manifest, work, None)
                initial_hash = None
                for _ in range(2):
                    output = subprocess.run(command, capture_output=True, timeout=40)
                    if output.returncode:
                        raise RuntimeError("エンジンの取得契約に失敗: " + kind)
                    events = [
                        parse_line(line)
                        for line in output.stdout.decode("utf-8").splitlines()
                        if line.startswith("DT_ITEM")
                    ]
                    if len(events) != 1 or not events[0] or not events[0].get("category"):
                        raise RuntimeError("エンジンの完成通知契約に失敗: " + kind)
                    if not all(
                        key in events[0] for key in ("identity", "site", "uploader", "title")
                    ):
                        raise RuntimeError("エンジンの共通メタデータ契約に失敗: " + kind)
                    path = Path(events[0]["path"])
                    if not path.resolve().is_relative_to(work.resolve()) or not path.stat().st_size:
                        raise RuntimeError("エンジンの保存パス契約に失敗: " + kind)
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                    if initial_hash and initial_hash != actual:
                        raise RuntimeError("既存ファイルの内容が変更されました")
                    initial_hash = actual
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
    print("動画・画像の取得／完成通知／既存ファイル契約: OK")


if __name__ == "__main__":
    verify(json.loads(sys.argv[1]))
