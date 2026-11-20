"""ローカルの固定ツールで検査し、非ゼロ終了・時間切れをそのまま不合格にする。"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def run_check(command, timeout, log, environment):
    started = time.perf_counter()
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            process.wait(timeout=30)
            psutil.wait_procs(children, timeout=10)
            code = 124
    return {
        "exit_code": code,
        "seconds": round(time.perf_counter() - started, 2),
        "log": log.relative_to(ROOT).as_posix(),
    }


def main():
    cache = ROOT / ".cache/verification"
    cache.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=cache))
    python = ROOT / ".venv/Scripts/python.exe"
    node = ROOT / "runtime/build-tools/node-v24.22.3-win-x64/node.exe"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "UV_CACHE_DIR": str(ROOT / ".cache/uv"),
        "UV_OFFLINE": "1",
    }
    checks = {
        "lint": ([str(python), "-m", "ruff", "check", "src", "tests", "scripts"], 60),
        "format": ([str(python), "-m", "ruff", "format", "--check", "src", "tests", "scripts"], 60),
        "types": (
            [
                str(node),
                str(ROOT / ".venv/Lib/site-packages/pyright/dist/index.js"),
                "--pythonpath",
                str(python),
            ],
            120,
        ),
        "dependencies": (
            [str(ROOT / "runtime/uv.exe"), "pip", "check", "--python", str(python)],
            60,
        ),
        "frontend-types": (
            [
                str(node),
                "frontend/node_modules/typescript/bin/tsc",
                "--noEmit",
                "--project",
                "frontend",
            ],
            60,
        ),
        "frontend-build": (
            [
                str(node),
                "frontend/node_modules/vite/bin/vite.js",
                "build",
                "frontend",
                "--config",
                "frontend/vite.config.ts",
            ],
            60,
        ),
        "tests": ([str(python), "-m", "pytest", "--basetemp=" + str(run / "tests")], 300),
        "benchmark": ([str(python), "scripts/benchmark.py"], 120),
    }
    results = {}
    for name, (command, timeout) in checks.items():
        results[name] = run_check(command, timeout, run / (name + ".log"), env)
        print(name + ": " + str(results[name]["exit_code"]), flush=True)
    (run / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("結果: " + run.relative_to(ROOT).as_posix())
    return int(any(item["exit_code"] for item in results.values()))


if __name__ == "__main__":
    sys.exit(main())
