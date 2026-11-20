"""実行環境の版でNVDとuv開発元の公開勧告を照合する。照合不能も非ゼロ終了。"""

import json
import operator
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from downloader_tools.database_driver import sqlite3

ROOT = Path(__file__).resolve().parents[1]
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "=": operator.eq}


def query(url):
    request = urllib.request.Request(url, headers={"User-Agent": "DownloaderTools-native-audit"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def installed(binary):
    text = subprocess.check_output(
        [str(binary), "-version" if binary.stem == "ffmpeg" else "--version"], timeout=20
    ).decode()
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", text.splitlines()[0].lstrip("v"))
    if not match:
        raise ValueError("実行ファイルの版を取得できません: " + binary.name)
    return match[1]


def affected(version, expression):
    for item in expression.split(","):
        match = re.fullmatch(r"\s*(<=|>=|<|>|=)?\s*(\d+\.\d+\.\d+)\s*", item)
        if not match:
            raise ValueError("未対応の比較式: " + expression)
        if not OPS[match[1] or "="](
            tuple(map(int, version.split("."))), tuple(map(int, match[2].split(".")))
        ):
            return False
    return True


def inspect(name, version):
    if name == "uv":
        url = "https://api.github.com/repos/astral-sh/uv/security-advisories?per_page=100"
        matches = []
        for page in range(1, 101):
            rows = query(url + "&page=" + str(page))
            for item in rows:
                if not item.get("withdrawn_at") and any(
                    affected(version, entry["vulnerable_version_range"])
                    for entry in item["vulnerabilities"]
                    if entry["package"]["name"] == "uv"
                ):
                    matches.append(item["ghsa_id"])
            if len(rows) < 100:
                return matches, url
        raise ValueError("取得上限を超えました")
    cpe = {
        "python": "python:python",
        "sqlite": "sqlite:sqlite",
        "node": "nodejs:node.js",
        "ffmpeg": "ffmpeg:ffmpeg",
    }[name]
    url = (
        "https://services.nvd.nist.gov/rest/json/cves/2.0?"
        + urllib.parse.urlencode({"cpeName": f"cpe:2.3:a:{cpe}:{version}:*:*:*:*:*:*:*"})
        + "&isVulnerable"
    )
    result = query(url)
    if len(result["vulnerabilities"]) != result["totalResults"]:
        raise ValueError("照合結果が1ページを超えました")
    return [row["cve"]["id"] for row in result["vulnerabilities"]], url


def main():
    windows = sys.platform == "win32"
    binaries = ROOT / "runtime"
    node = (
        binaries
        / "build-tools"
        / ("node-v24.22.3-win-x64/node.exe" if windows else "node/bin/node")
    )
    versions = {
        "python": ".".join(map(str, sys.version_info[:3])),
        "sqlite": sqlite3.sqlite_version,
        "node": installed(node),
        "ffmpeg": installed(binaries / "media" / ("ffmpeg.exe" if windows else "ffmpeg")),
        "uv": installed(binaries / ("uv.exe" if windows else "uv")),
    }
    report = {"checked_at": datetime.now(UTC).isoformat(), "components": {}}
    failed = False
    for name, version in versions.items():
        row = {"version": version}
        report["components"][name] = row
        try:
            ids, row["source"] = inspect(name, version)
            reviewed = {}
            if name == "ffmpeg" and version in {"9.1", "9.1.0"}:
                for key in ("CVE-2025-25468", "CVE-2025-25469"):
                    reviewed[key] = "修正d5873be583ada9e1fb887e2fe8dcfd4b12e0efcdはn9.1の祖先"
            if name == "sqlite" and version == "3.54.1":
                reviewed["CVE-2026-119586"] = "CLIの.recoverが対象。アプリはDB-APIのみを使用"
            row["reviewed"] = {key: reviewed[key] for key in ids if key in reviewed}
            row["unresolved"] = [key for key in ids if key not in reviewed]
            failed |= bool(row["unresolved"])
        except (OSError, ValueError, KeyError) as exc:
            row["error"] = str(exc)
            failed = True
        print(name, json.dumps(row, ensure_ascii=True))
    path = ROOT / ".cache/native-audit.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
