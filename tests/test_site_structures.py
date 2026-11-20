import json

import pytest

from downloader_tools.engine_metadata import completion, gallery_identity, video_identity


@pytest.mark.parametrize(
    "metadata,author,title",
    [
        ({"author": "作者", "title": "作品"}, "作者", "作品"),
        (
            {
                "data": {
                    "node": {
                        "user": {"profile": {"display_name": "作者"}},
                        "caption": {"text": "作品"},
                    }
                }
            },
            "作者",
            "作品",
        ),
        (
            {"attributes": {"artist": [{"name": "作者"}], "title": {"rendered": "作品"}}},
            "作者",
            "作品",
        ),
        ({"_parent": {"account": {"handle": "作者"}, "subject": "作品"}}, "作者", "作品"),
        ({"id": 123, "filename": "作品"}, "", "作品"),
        ({}, "", ""),
    ],
)
def test_metadata_shapes_are_normalized(metadata, author, title):
    event = completion(metadata, "new-platform", "https://new.example/post", "a" * 64)
    assert event["uploader"] == author and event["title"] == title


def test_large_and_recursive_metadata_remains_bounded():
    metadata = {"description": "あ" * 100_000, "author": {"data": {"name": "作者"}}}
    metadata["cycle"] = metadata
    event = completion(metadata, "new-platform", "https://new.example/post", "a" * 64)
    assert len(json.dumps(event, ensure_ascii=True)) < 4096
    assert completion({"cycle": metadata}, "new", "", "b" * 64)["uploader"] == "作者"


def test_asset_identity_handles_long_ids_missing_ids_and_reused_numbers():
    info = {
        "extractor_key": "NewExtractor",
        "webpage_url": "https://a.example/post",
        "id": "a" * 10_000,
    }
    first = video_identity(info)
    assert first != video_identity({**info, "id": info["id"] + "b"})
    assert first != video_identity({**info, "webpage_url": "https://b.example/post"})
    generic = {"extractor_key": "Generic", "id": "sample"}
    assert video_identity({**generic, "webpage_url": "https://a.example/one/sample.mp4"}) != (
        video_identity({**generic, "webpage_url": "https://a.example/two/sample.mp4"})
    )
    assert video_identity({**generic, "webpage_url": "https://a.example/post", "url": "one"}) != (
        video_identity({**generic, "webpage_url": "https://a.example/post", "url": "two"})
    )
    for metadata in ({}, {"id": 1, "num": 1, "filename": "same"}):
        urls = ["https://cdn.example/image?asset=1", "https://cdn.example/image?asset=2"]
        assert (
            len({gallery_identity("new", "https://a.example/album", url, metadata) for url in urls})
            == 2
        )


def test_unicode_and_separator_equivalent_output_paths_collide():
    from downloader_tools.publisher import output_path_key, safe_title

    assert output_path_key("Site/作者/Ä.png") == output_path_key("site\\作者\\a\u0308.png")
    assert safe_title("bad\ud800name") == "bad name"
