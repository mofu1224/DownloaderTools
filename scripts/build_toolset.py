"""lock済みエンジンを独立した不変環境へ構築し、契約検証後に登録する。"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    uv = ROOT / "runtime" / ("uv.exe" if os.name == "nt" else "uv")
    media = ROOT / "runtime/media"
    ffmpeg = media / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    ffprobe = media / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    node = (
        next((ROOT / "runtime/build-tools").glob("node-*/node.exe"))
        if os.name == "nt"
        else ROOT / "runtime/build-tools/node/bin/node"
    )
    binary_digest = hashlib.sha256()
    for binary in (ffmpeg, ffprobe, node):
        with binary.open("rb") as stream:
            binary_digest.update(hashlib.file_digest(stream, "sha256").digest())
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(ROOT / ".cache/uv"),
        "UV_PYTHON_BIN_DIR": str(ROOT / "runtime/bin"),
        "UV_PYTHON_INSTALL_DIR": str(ROOT / "runtime/python"),
    }
    if os.environ.get("DT_OFFLINE") == "1":
        env["UV_OFFLINE"] = "1"
    requirements = ROOT / ".cache/engine-requirements.txt"
    if "--refresh" in sys.argv:
        packages = []
        for package in ("yt-dlp", "gallery-dl"):
            request = urllib.request.Request(
                f"https://pypi.org/pypi/{package}/json",
                headers={"User-Agent": "DownloaderTools/0.1"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                info = json.load(response)
            version = info["info"]["version"]
            if not all(c.isalnum() or c in ".-" for c in version):
                raise RuntimeError("配布版の形式が不正です")
            packages.append(package + ("[default]" if package == "yt-dlp" else "") + "==" + version)
        specification = ROOT / ".cache/engine-update.in"
        specification.write_text("\n".join(packages), encoding="utf-8")
        requirements = ROOT / ".cache/engine-update.txt"
        subprocess.run(
            [
                str(uv),
                "pip",
                "compile",
                "--no-config",
                "--default-index",
                "https://pypi.org/simple",
                "--generate-hashes",
                "--python-version",
                "3.14",
                "--output-file",
                str(requirements),
                str(specification),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
            stdout=subprocess.DEVNULL,
        )
    lock_digest = hashlib.sha256(
        (ROOT / "uv.lock").read_bytes() + binary_digest.digest() + b"toolset-v4"
    ).hexdigest()[:12]
    identity = "locked-" + lock_digest
    if "--refresh" in sys.argv:
        identity = (
            "updated-"
            + hashlib.sha256(
                requirements.read_bytes() + binary_digest.digest() + b"toolset-v4"
            ).hexdigest()[:12]
        )
    destination = ROOT / "runtime/toolsets" / identity
    if (destination / "manifest.json").exists():
        print("検証済みtoolset: " + identity)
        return identity
    destination.mkdir(parents=True, exist_ok=True)
    python = destination / "python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(uv),
            "venv",
            "--python",
            str(Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")),
            str(destination / "python"),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=60,
    )
    if "--refresh" not in sys.argv:
        subprocess.run(
            [
                str(uv),
                "export",
                "--locked",
                "--only-group",
                "engines",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=60,
            stdout=subprocess.DEVNULL,
        )
    subprocess.run(
        [str(uv), "pip", "sync", "--python", str(python), "--require-hashes", str(requirements)],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=300,
    )
    versions = json.loads(
        subprocess.check_output(
            [str(python), "-I", str(ROOT / "scripts/contract_probe.py")],
            cwd=ROOT,
            env=env,
            timeout=90,
        )
    )
    binary_dir = destination / "bin"
    binary_dir.mkdir(exist_ok=True)
    for original in (ffmpeg, ffprobe, node):
        shutil.copyfile(original, binary_dir / original.name)
    files = {
        "python": python,
        "ffmpeg": binary_dir / ffmpeg.name,
        "ffprobe": binary_dir / ffprobe.name,
        "node": binary_dir / node.name,
    }
    for name in ("ffmpeg", "ffprobe", "node"):
        output = subprocess.check_output(
            [str(files[name]), "--version" if name == "node" else "-version"], timeout=30
        )
        versions[name] = output.decode().splitlines()[0]
    relative = {key: str(path.relative_to(ROOT)) for key, path in files.items()}
    subprocess.run(
        [sys.executable, "-m", "downloader_tools.engine_contract", json.dumps(relative)],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=180,
    )
    hashes = {}
    for path in files.values():
        with path.open("rb") as stream:
            hashes[str(path.relative_to(ROOT))] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "id": identity,
        **relative,
        "hashes": hashes,
        "versions": versions,
        "contracts": 1,
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
    }
    base_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    with base_python.open("rb") as stream:
        manifest["base_python_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    shutil.copyfile(requirements, destination / "requirements.txt")
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("契約検証を完了: " + identity)
    return identity


if __name__ == "__main__":
    identity = main()
    if "--result-file" in sys.argv:
        result = Path(sys.argv[sys.argv.index("--result-file") + 1]).resolve()
        if not result.is_relative_to((ROOT / ".cache").resolve()):
            raise RuntimeError("結果ファイルは.cache内に指定してください")
        result.write_text(json.dumps({"id": identity}), encoding="utf-8")
