"""組立済みのアプリを起動・認証・診断し、正常終了する。"""

import socket
import sys
import threading
import time

import httpx
import uvicorn

from downloader_tools.web import create_app


def main():
    app = create_app()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 8765))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "起動失敗"
        with httpx.Client(base_url="http://127.0.0.1:8765") as client:
            assert client.get("/api/v1/health").json()["status"] == "ok"
            session = client.post("/api/v1/session", json={"token": app.state.bootstrap_token})
            assert session.status_code == 200
            headers = {"X-CSRF-Token": session.json()["csrf"]}
            diagnostics = client.get("/api/v1/diagnostics").json()
            assert any(t["active"] for t in diagnostics["tools"])
            assert client.get("/").status_code == 200
            print("起動・セッション・診断・HTML応答: OK")
            print("SQLite: " + diagnostics["sqlite"])
            for tool in diagnostics["tools"]:
                if tool["active"]:
                    print("有効toolset: " + tool["id"])
                    previous = tool["id"]
            for tool in diagnostics["tools"]:
                if not tool["active"]:
                    assert (
                        client.post(
                            "/api/v1/toolset-updates",
                            headers=headers,
                            json={"toolset_id": tool["id"]},
                        ).status_code
                        == 202
                    )
                    assert (
                        client.post(
                            "/api/v1/toolset-updates",
                            headers=headers,
                            json={"toolset_id": previous},
                        ).status_code
                        == 202
                    )
                    print("toolset検証・切替・切戻し: OK")
                    break
            if "--update" in sys.argv:
                job = client.post("/api/v1/toolset-updates", headers=headers, json={})
                assert job.status_code == 202
                for _ in range(600):
                    result = client.get("/api/v1/toolset-updates/" + job.json()["id"]).json()
                    if result["state"] != "running":
                        break
                    time.sleep(0.1)
                assert result["state"] == "completed", result
                assert (
                    client.post(
                        "/api/v1/toolset-updates", headers=headers, json={"toolset_id": previous}
                    ).status_code
                    == 202
                )
                print("バックグラウンド更新API・検証後切替・切戻し: OK")
    finally:
        server.should_exit = True
        thread.join(20)
        listener.close()
        assert not thread.is_alive(), "正常終了失敗"


if __name__ == "__main__":
    main()
