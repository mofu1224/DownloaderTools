import json
from datetime import date

import pytest

from downloader_tools.domain import Profile
from downloader_tools.gallery_dates import UNKNOWN, bounds, skip_reason
from downloader_tools.publisher import Publisher
from downloader_tools.store import Store


def test_open_ends_use_post_offset_not_machine_timezone():
    day = date(2026, 1, 2)
    before = {"create_date": "2026-01-01T14:59:59Z"}
    start = bounds(day, None, "Asia/Tokyo")
    end = bounds(None, day, "Asia/Tokyo")
    assert skip_reason("pixiv", "work", before, start) == "DATE_OUTSIDE_RANGE"
    assert skip_reason("pixiv", "work", before, end) is None
    assert skip_reason("pixiv", "work", {"create_date": "2026-01-02"}, end) == UNKNOWN
    assert skip_reason("pixiv", "work", {}, end) == UNKNOWN
    assert skip_reason("other", "work", before, end) == UNKNOWN


async def test_batch_requires_verified_site_and_version_and_counts_unknown_once(tmp_path):
    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        await store.register_root("default", tmp_path)
        profile = await store.add_profile(
            Profile(kind="gallery", date_to=date(2026, 1, 2)).model_dump(mode="json")
        )
        batch = {
            "kind": "gallery",
            "profile_id": profile["id"],
            "urls": ["https://www.pixiv.net/artworks/123"],
        }
        await store.write(lambda c: c.execute("INSERT INTO toolsets VALUES ('tool', '{}', 1)"))
        with pytest.raises(ValueError, match="DATE_CONTRACT_UNVERIFIED"):
            await store.create_batch(batch, "wrong-version")
        await store.write(
            lambda c: c.execute(
                "UPDATE toolsets SET manifest=?",
                (json.dumps({"versions": {"gallery-dl": "1.32.16"}}),),
            )
        )
        with pytest.raises(ValueError, match="DATE_CONTRACT_UNVERIFIED"):
            await store.create_batch(
                {**batch, "urls": batch["urls"] + ["https://example.org/artworks/123"]}, "mixed"
            )
        result = await store.create_batch(batch, "ok")
        source = await store.source(result["sources"][0]["id"])
        publisher = Publisher(store)
        event = {"policy_skip": UNKNOWN, "media_key": "test-key", "path": ""}
        await publisher.publish(source, event)
        await publisher.publish(source, event)
        assert (await store.source(source["id"]))["skipped_count"] == 1
        assert await store.read("SELECT reason FROM policy_skips") == [{"reason": UNKNOWN}]
    finally:
        await store.close()


async def test_pixiv_credential_is_private_and_rejected_for_other_sites(tmp_path):
    from downloader_tools.credentials import Credentials

    store = Store(tmp_path / "data/app.sqlite3")
    await store.start()
    try:
        credentials = Credentials(store, tmp_path)
        auth = await credentials.create(
            "test", json.dumps({"type": "pixiv-oauth", "refresh_token": "fixture-oauth-only"})
        )
        assert "fixture-oauth-only" not in json.dumps([auth, await credentials.list()])
        with pytest.raises(ValueError, match="PIXIV_CREDENTIAL_SCOPE"):
            await store.create_batch(
                {
                    "profile_id": "gallery-default",
                    "kind": "gallery",
                    "urls": ["https://example.org/artworks/1"],
                    "credential_id": auth["id"],
                },
                "mixed-auth",
            )
        assert not await store.read("SELECT id FROM sources")
    finally:
        await store.close()
