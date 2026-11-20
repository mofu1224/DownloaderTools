import socket
import threading
import time

from playwright.sync_api import sync_playwright


def test_browser_create_pause_history_settings_and_mobile(tmp_path):
    import uvicorn

    from downloader_tools.paths import root_path
    from downloader_tools.web import create_app

    listener = None
    for port in range(8765, 8785):
        candidate = socket.socket()
        try:
            candidate.bind(("127.0.0.1", port))
            candidate.listen()
            listener = candidate
            break
        except OSError:
            candidate.close()
    assert listener
    app = create_app(tmp_path, bootstrap_token="browser-fixture", start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/#token=browser-fixture")
            page.get_by_text("接続済み", exact=True).wait_for()
            page.get_by_label("URL", exact=False).fill("https://example.com/video?signature=secret")
            theme = page.get_by_role("button", name="ダークモード", exact=True)
            previous = theme.get_attribute("aria-pressed")
            theme.focus()
            page.keyboard.press("Space")
            selected = theme.get_attribute("aria-pressed")
            assert selected != previous
            assert page.locator("#urls").input_value().endswith("signature=secret")
            page.get_by_role("button", name="＋ キューに追加").click()
            page.locator(".source-card").wait_for()
            assert "signature=secret" not in page.locator("#queue-list").inner_text()
            page.get_by_role("button", name="一時停止", exact=True).click()
            page.get_by_text("一時停止", exact=True).wait_for()
            from playwright.sync_api import expect

            source_id = page.locator(".source-card").get_attribute("data-id")
            log = tmp_path / "data/logs" / (source_id + ".log")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("取得開始\n", encoding="utf-8")
            page.get_by_role("button", name="詳細", exact=True).click()
            expect(page.locator("#detail-log")).to_contain_text("取得開始")
            with log.open("a", encoding="utf-8") as stream:
                stream.write("転送中 12345 bytes\n")
            expect(page.locator("#detail-log")).to_contain_text("転送中 12345 bytes")
            page.get_by_role("button", name="詳細を閉じる", exact=True).click()
            screenshots = root_path() / ".cache/screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "desktop.png"), full_page=True)
            page.get_by_role("button", name="履歴", exact=False).click()
            page.locator("#history-list .source-card").wait_for()
            page.get_by_role("button", name="設定", exact=False).click()
            page.get_by_label("全体の並列数").fill("3")
            page.get_by_role("button", name="設定を保存").click()
            page.get_by_text("実行予算を保存しました。", exact=True).wait_for()
            page.get_by_role("button", name="ダウンロード", exact=False).click()
            page.set_viewport_size({"width": 320, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
            page.reload()
            assert theme.get_attribute("aria-pressed") == selected
            page.get_by_text("接続済み", exact=True).wait_for(state="attached")
            page.get_by_label("URL", exact=False).fill("file:///invalid-fixture")
            page.get_by_role("button", name="＋ キューに追加").click()
            expect(page.locator("#notice")).to_contain_text("入力を確認してください")
            assert page.get_by_label("URL", exact=False).input_value() == "file:///invalid-fixture"
            page.get_by_role("button", name="詳細", exact=True).click()
            expect(page.locator("#detail-dialog")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator("#detail-dialog")).not_to_be_visible()
            page.get_by_label("URL", exact=False).focus()
            page.keyboard.press("Tab")
            assert page.locator(":focus").get_attribute("id") == "options-toggle"
            expect(page.locator("#container")).not_to_be_visible()
            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            assert page.locator(":focus").get_attribute("id") == "container"
            page.get_by_label("画像・アルバム").check()
            expect(page.locator("#date-from")).to_be_disabled()
            page.locator("#urls").fill("https://www.pixiv.net/artworks/123")
            page.get_by_label("開始日").fill("2026-11-01")
            page.get_by_label("終了日").fill("2026-11-01")
            page.get_by_label("タイムゾーン").fill("America/New_York")
            with page.expect_response("**/api/v1/profiles") as created:
                page.get_by_role("button", name="＋ キューに追加").click()
            profile = created.value.json()
            assert profile["date_from"] == profile["date_to"] == "2026-11-01"
            assert profile["timezone"] == "America/New_York"
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        worker.join(10)
        listener.close()
        assert not worker.is_alive()
