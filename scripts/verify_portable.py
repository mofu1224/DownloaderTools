"""クローン相当のクリーンコピー、または環境移設後の準備と起動を検証する。

- 公開予定のファイルだけをコピーする（data/・downloads/・開発用キャッシュは含めない）。
- 日本語と空白を含む別フォルダへ移し、別のカレントディレクトリから起動する。
- PATHから開発端末のpython/node/uvを除いた状態で、同梱物だけで準備・起動できるか見る。
- オフライン段ではHTTP(S)_PROXYを到達不能な値にして、意図しない通信を失敗させる。
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
PUBLISHED_FILES = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "Start-DownloaderTools.bat",
)
SOURCE_TREES = {
    "src": {".py", ".sql", ".html", ".css", ".js"},
    "scripts": {".py", ".ps1", ".sh", ".json"},
    "tests": {".py"},
    "frontend": {".ts", ".css", ".json"},
}
BUNDLED_DIRS = ("runtime", ".cache/uv", ".cache/distributions")
IGNORE = shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc", "dist", ".pytest_cache")


def copy_entry(source, destination):
    if source.is_dir():
        shutil.copytree(
            source, destination, ignore=IGNORE, copy_function=shutil.copyfile, dirs_exist_ok=True
        )
    else:
        shutil.copyfile(source, destination)


def build_copy(destination, source_only):
    destination.mkdir(parents=True)
    files = [ROOT / name for name in PUBLISHED_FILES]
    for tree, extensions in SOURCE_TREES.items():
        for path in (ROOT / tree).rglob("*"):
            if any(part in ("node_modules", "__pycache__", "dist") for part in path.parts):
                continue
            if path.is_file() and path.suffix in extensions:
                files.append(path)
    manifest = {}
    for path in sorted(files):
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("検証入力が欠落またはフォルダ外を参照しています")
        current = path
        while current != ROOT:
            if current.is_symlink() or current.is_junction():
                raise ValueError("検証入力のリンクは使用できません")
            current = current.parent
        name = path.relative_to(ROOT).as_posix()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_entry(path, target)
        with target.open("rb") as stream:
            manifest[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    if not source_only:
        for name in BUNDLED_DIRS:
            copy_entry(ROOT / name, destination / name)
    return {
        "input_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "files": manifest,
    }


def clean_environment(offline):
    env = {key: value for key, value in os.environ.items()}
    for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT"):
        env.pop(key, None)
    system = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join(
        (
            str(Path(system) / "System32"),
            str(Path(system) / "System32/WindowsPowerShell/v1.0"),
            str(Path(system)),
        )
    )
    env["PYTHONUTF8"] = "1"
    if offline:
        # 到達不能なプロキシに向け、意図しない通信を早期に失敗させる。
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        env["DT_OFFLINE"] = "1"
    return env


def run(command, cwd, env, timeout):
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(process)
        raise
    if process.returncode != 0:
        text = output.decode("utf-8", "replace")
        raise RuntimeError(f"コマンド失敗({process.returncode}): {' '.join(command)}\n{text}")


def fetch(url):
    with OPENER.open(url, timeout=5) as response:
        return response.status, response.read()


def wait_until_ready(destination, deadline):
    launch = destination / "data/instance/launch.json"
    while time.time() < deadline:
        if launch.exists():
            try:
                base = json.loads(launch.read_text())["url"].split("#")[0]
                if fetch(base + "api/v1/health")[0] == 200:
                    return base
            except OSError, json.JSONDecodeError, urllib.error.URLError, KeyError:
                pass
        time.sleep(0.5)
    raise RuntimeError("起動待ちがタイムアウトしました")


def verify_runtime(destination, foreign):
    """移設後のパス参照とアプリ起動が本来の場所を向くか確認する。"""
    stamp = destination / "runtime/environment-root.txt"
    assert stamp.read_text(encoding="utf-8").strip() == str(destination), "環境ルート記録が不正"
    config = destination / ".venv/pyvenv.cfg"
    assert config.is_file(), ".venvがありません"
    home = next(
        line.split("=", 1)[1].strip()
        for line in config.read_text(encoding="utf-8").splitlines()
        if line.startswith("home")
    )
    assert home.startswith(str(destination)), "仮想環境が移設先を向いていません: " + home
    python = destination / ".venv/Scripts/python.exe"
    run(
        [str(python), "-c", "import downloader_tools,sys;print(sys.executable)"],
        cwd=foreign,
        env=clean_environment(True),
        timeout=60,
    )
    for manifest in (destination / "runtime/toolsets").glob("*/manifest.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for key in ("python", "ffmpeg", "ffprobe", "node"):
            assert not Path(data[key]).is_absolute(), "toolset参照が絶対パス: " + data[key]
            assert (destination / data[key]).is_file(), "toolset参照が欠落: " + data[key]


def stop(process):
    import psutil

    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.kill()
        parent.kill()
        psutil.wait_procs(children, timeout=10)
    except psutil.Error:
        pass
    process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--source-only", action="store_true", help="同梱物なしの取得前提で検証")
    parser.add_argument(
        "--checks", action="store_true", help="コピー内でフロントを準備し全検証を実行"
    )
    parser.add_argument("--online", action="store_true", help="起動スクリプト経由の再構築も検証")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    destination = args.destination or (ROOT / ".cache/DownloaderTools ポータブル検証")
    if destination.exists():
        raise SystemExit("検証先が残っています: " + str(destination))
    offline = not args.source_only
    environment = clean_environment(offline)
    foreign = ROOT / ".cache/foreign-cwd"
    foreign.mkdir(parents=True, exist_ok=True)
    bat = destination / "Start-DownloaderTools.bat"
    try:
        inputs = build_copy(destination, args.source_only)
        report = Path(tempfile.mkdtemp(prefix="clone-checks-", dir=ROOT / ".cache"))
        (report / "inputs.json").write_text(json.dumps(inputs, indent=2), encoding="utf-8")
        print("検証先: " + str(destination))
        if offline:
            # 到達不能プロキシ＋--offlineで、同梱物だけで準備できることを強制検証する。
            run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(destination / "scripts/bootstrap.ps1"),
                    "-Offline",
                ],
                cwd=foreign,
                env=environment,
                timeout=args.timeout,
            )
            verify_runtime(destination, foreign)
            print("オフライン準備（移設再構築・参照・import）: OK")
        else:
            run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(destination / "scripts/bootstrap.ps1"),
                ],
                cwd=foreign,
                env=environment,
                timeout=args.timeout,
            )
            print("オンライン準備（取得前提）: OK")
        if args.online:
            (destination / "runtime/environment-root.txt").unlink()
        if args.checks:
            if offline:
                raise ValueError("--checksは--source-onlyと併用してください")
            run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(destination / "scripts/bootstrap.ps1"),
                    "-BuildFrontend",
                ],
                cwd=foreign,
                env=environment,
                timeout=args.timeout,
            )
            run(
                [
                    str(destination / ".venv/Scripts/python.exe"),
                    str(destination / "scripts/verify.py"),
                ],
                cwd=foreign,
                env=environment,
                timeout=600,
            )
            latest = max(
                (destination / ".cache/verification").glob("run-*"),
                key=lambda path: path.stat().st_mtime,
            )
            for path in latest.iterdir():
                if path.is_file() and path.suffix in (".json", ".log"):
                    shutil.copyfile(path, report / path.name)
            summary = json.loads((report / "results.json").read_text(encoding="utf-8"))
            for result in summary.values():
                result["log"] = (report / Path(result["log"]).name).relative_to(ROOT).as_posix()
            (report / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            shutil.copyfile(destination / ".cache/benchmark.json", report / "benchmark.json")
            print("クローン相当の検証記録: " + report.relative_to(ROOT).as_posix())
            print("クリーンコピー内の全検証（ビルド・pytest・型・Lint・依存・ベンチマーク）: OK")
        process = subprocess.Popen(
            ["cmd.exe", "/c", str(bat), "--no-browser"],
            cwd=foreign,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base = wait_until_ready(destination, time.time() + 180)
            token = json.loads((destination / "data/instance/launch.json").read_text())["url"]
            token = token.split("#token=", 1)[1]
            request = urllib.request.Request(
                base + "api/v1/session",
                data=json.dumps({"token": token}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with OPENER.open(request, timeout=5) as response:
                assert response.status == 200
            assert fetch(base)[0] == 200
            active = json.loads(fetch(base + "api/v1/diagnostics")[1])
            assert any(tool["active"] for tool in active["tools"]), "有効なtoolsetがありません"
            print("起動・セッション・診断・HTML応答（別CWD・非グローバルPATH）: OK")
        finally:
            stop(process)
        print("ポータブル検証: OK")
    finally:
        if destination.exists():
            shutil.rmtree(destination)


if __name__ == "__main__":
    sys.exit(main())
