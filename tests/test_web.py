import json

from fastapi.testclient import TestClient


def authenticated(client, token):
    response = client.post("/api/v1/session", json={"token": token})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf"]}


def test_api_auth_csrf_idempotency_and_snapshot_redaction(tmp_path):
    from downloader_tools.web import create_app

    app = create_app(tmp_path, bootstrap_token="test-token", start_scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/v1/settings").status_code == 401
        headers = authenticated(client, "test-token")
        assert client.patch("/api/v1/settings", json={}).status_code == 403
        request = {
            "kind": "video",
            "urls": ["https://example.com/watch?secret=do-not-expose"],
            "profile_id": "video-default",
            "output_root_id": "default",
            "credential_id": None,
        }
        headers["Idempotency-Key"] = "test-key"
        first = client.post("/api/v1/batches", json=request, headers=headers)
        assert first.status_code == 202
        assert client.post("/api/v1/batches", json=request, headers=headers).json() == first.json()
        snapshot = client.get("/api/v1/snapshot")
        assert "do-not-expose" not in snapshot.text
        assert len(snapshot.json()["sources"]) == 1
        source = first.json()["sources"][0]["id"]
        log = tmp_path / "data/logs" / (source + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        text = "x" * 65535 + "取得中😀\n"
        log.write_bytes(text.encode("utf-8"))
        head = client.get(f"/api/v1/sources/{source}/logs").json()
        tail = client.get(f"/api/v1/sources/{source}/logs?offset={head['next_offset']}").json()
        assert head["text"] + tail["text"] == text
        assert (
            client.post(f"/api/v1/sources/{source}/pause", headers=headers).json()["state"]
            == "paused"
        )
        assert (
            client.post(f"/api/v1/sources/{source}/cancel", headers=headers).json()["state"]
            == "cancelled"
        )
        assert client.post(f"/api/v1/sources/{source}/resume", headers=headers).status_code == 409
        assert client.get("/", headers={"host": "evil.example"}).status_code == 400
        assert (
            client.post(
                "/api/v1/batches",
                json=request,
                headers={**headers, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )


def test_cookie_is_immutable_and_referenced_credential_cannot_be_deleted(tmp_path):
    from downloader_tools.web import create_app

    with TestClient(
        create_app(tmp_path, bootstrap_token="token", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        headers = authenticated(client, "token")
        cookie = [
            {"domain": ".example.com", "name": "session", "value": "secret-value", "path": "/"}
        ]
        response = client.post(
            "/api/v1/credentials",
            headers=headers,
            json={"name": "テスト", "content": json.dumps(cookie)},
        )
        assert response.status_code == 201
        credential_id = response.json()["id"]
        assert "secret-value" not in client.get("/api/v1/credentials").text
        batch = client.post(
            "/api/v1/batches",
            headers={**headers, "Idempotency-Key": "a"},
            json={
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "credential_id": credential_id,
            },
        )
        assert batch.status_code == 202
        assert (
            client.delete("/api/v1/credentials/" + credential_id, headers=headers).status_code
            == 409
        )
        assert b"secret-value" in next((tmp_path / "data/credentials").glob("*.txt")).read_bytes()


def test_settings_revision_and_backup(tmp_path):
    from downloader_tools.web import create_app

    with TestClient(
        create_app(tmp_path, bootstrap_token="token", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        headers = authenticated(client, "token")
        settings = client.get("/api/v1/settings").json()
        assert (
            client.patch(
                "/api/v1/settings", headers=headers, json={**settings, "total_slots": 3}
            ).status_code
            == 200
        )
        assert client.patch("/api/v1/settings", headers=headers, json=settings).status_code == 409
        assert client.post("/api/v1/backups", headers=headers).status_code == 201
        import sqlite3

        backup = next((tmp_path / "data/backups").glob("*.sqlite3"))
        with sqlite3.connect(backup) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_deleted_cookie_requires_replacement_for_retry(tmp_path):
    from downloader_tools.web import create_app

    with TestClient(
        create_app(tmp_path, bootstrap_token="token", start_scheduler=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        headers = authenticated(client, "token")
        cookie = client.post(
            "/api/v1/credentials",
            headers=headers,
            json={
                "name": "テスト",
                "content": json.dumps(
                    [{"domain": ".example.com", "name": "session", "value": "private"}]
                ),
            },
        ).json()
        batch = client.post(
            "/api/v1/batches",
            headers={**headers, "Idempotency-Key": "original"},
            json={
                "kind": "gallery",
                "urls": ["https://example.com/a"],
                "profile_id": "gallery-default",
                "credential_id": cookie["id"],
            },
        ).json()
        source = batch["sources"][0]["id"]
        client.post(f"/api/v1/sources/{source}/cancel", headers=headers)
        assert (
            client.delete("/api/v1/credentials/" + cookie["id"], headers=headers).status_code == 200
        )
        assert not client.get("/api/v1/credentials").json()
        response = client.post(
            "/api/v1/retries",
            headers={**headers, "Idempotency-Key": "retry"},
            json={"source_id": source},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CREDENTIAL_REPLACEMENT_REQUIRED"
