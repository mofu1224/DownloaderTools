"""1,000未完了Sourceで操作API応答を測る。外部転送・24時間試験は含まない。"""

import json
import math
import tempfile
import time
from pathlib import Path

import psutil
from fastapi.testclient import TestClient

from downloader_tools.paths import root_path
from downloader_tools.web import create_app


def main():
    cache = root_path() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=cache) as folder:
        app = create_app(Path(folder), bootstrap_token="benchmark-fixture", start_scheduler=False)
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            session = client.post("/api/v1/session", json={"token": "benchmark-fixture"}).json()
            headers = {"X-CSRF-Token": session["csrf"]}
            source_ids = []
            for batch in range(10):
                response = client.post(
                    "/api/v1/batches",
                    headers={**headers, "Idempotency-Key": str(batch)},
                    json={
                        "kind": "gallery",
                        "profile_id": "gallery-default",
                        "urls": [f"https://example.com/{batch}/{n}" for n in range(100)],
                    },
                )
                assert response.status_code == 202
                source_ids.extend(s["id"] for s in response.json()["sources"])
            times = []
            for n in range(100):
                before = time.perf_counter()
                response = client.post(f"/api/v1/sources/{source_ids[n]}/pause", headers=headers)
                times.append((time.perf_counter() - before) * 1000)
                assert response.status_code == 200
            snapshot = client.get("/api/v1/snapshot")
            assert len(snapshot.json()["sources"]) == 1000
            result = {
                "unfinished_sources": 1000,
                "requests": 100,
                "operation_api_p95_ms": round(sorted(times)[math.ceil(len(times) * 0.95) - 1], 2),
                "process_rss_mib": round(psutil.Process().memory_info().rss / 1024**2, 2),
                "snapshot_bytes": len(snapshot.content),
                "scope": "ローカルASGI・SQLite操作。転送負荷なし。",
            }
            assert result["operation_api_p95_ms"] < 300
            assert result["process_rss_mib"] < 250
    (cache / "benchmark.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
