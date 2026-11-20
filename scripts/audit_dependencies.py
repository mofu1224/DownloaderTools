"""uv.lockの公開パッケージ名と版だけをPyPIのアドバイザリ情報と照合する。"""

import json
import sys
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect(package):
    name, version = package["name"], package["version"]
    try:
        request = urllib.request.Request(
            f"https://pypi.org/pypi/{name}/{version}/json",
            headers={"User-Agent": "DownloaderTools-dependency-audit/0.1"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        return {
            "name": name,
            "version": version,
            "advisories": [
                {
                    key: advisory.get(key)
                    for key in ("id", "aliases", "fixed_in", "link", "withdrawn")
                }
                for advisory in data.get("vulnerabilities", [])
                if not advisory.get("withdrawn")
            ],
        }
    except OSError, ValueError:
        return {"name": name, "version": version, "error": "ADVISORY_LOOKUP_FAILED"}


def main():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = [p for p in lock["package"] if "registry" in p.get("source", {})]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(inspect, packages))
    report = ROOT / ".cache/dependency-audit.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    findings = [r for r in results if r.get("advisories") or r.get("error")]
    print(json.dumps({"checked": len(results), "findings": findings}, indent=2))
    return int(bool(findings))


if __name__ == "__main__":
    sys.exit(main())
