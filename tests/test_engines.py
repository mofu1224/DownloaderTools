import json
import subprocess
import sys


def test_command_isolated_and_mp4_prioritizes_resolution(tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command

    source = {
        "id": "abc",
        "kind": "video",
        "url": "https://example.com/a",
        "profile": Profile(kind="video", container="mp4").model_dump_json(),
    }
    manifest = {
        "python": sys.executable,
        "ffmpeg": str(tmp_path / "ffmpeg.exe"),
        "node": str(tmp_path / "node.exe"),
    }
    command = build_command(source, manifest, tmp_path, None)
    assert "--ignore-config" in command and "--no-plugin-dirs" in command
    assert "--retries" in command and command[command.index("--retries") + 1] == "2"
    assert command[command.index("-f") + 1] == "bv*[height<=?1080]+ba/b[height<=?1080]"
    assert "--dt-mp4-height" in command
    assert "--recode-video" not in command
    assert command[-1] == "https://example.com/a"


def test_mp4_selects_highest_resolution_before_compatibility_conversion(tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command

    source = {
        "id": "abc",
        "kind": "video",
        "url": "https://example.com/a",
        "profile": Profile(kind="video", container="mp4", height=2160).model_dump_json(),
    }
    command = build_command(
        source,
        {"python": sys.executable, "ffmpeg": "f", "node": "n"},
        tmp_path,
        None,
    )

    format_selector = command[command.index("-f") + 1]
    assert format_selector == "bv*[height<=?2160]+ba/b[height<=?2160]"
    assert "vcodec^=avc1" not in format_selector
    assert "acodec^=mp4a" not in format_selector


def test_video_command_embeds_thumbnail_and_reports_title(tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command

    source = {
        "id": "abc",
        "kind": "video",
        "url": "https://example.com/a",
        "profile": Profile(kind="video").model_dump_json(),
    }
    manifest = {"python": sys.executable, "ffmpeg": "f", "node": "n"}
    command = build_command(source, manifest, tmp_path, None)
    assert "--embed-thumbnail" in command
    assert "%(dt_completion)j" in command[command.index("--print") + 1]
    assert "%(dt_storage)s" in command[command.index("-o") + 1]
    assert "--output-na-placeholder" in command


def test_video_resume_keeps_thumbnail_processing_for_new_items(tmp_path):
    from downloader_tools.domain import Profile
    from downloader_tools.engines import build_command

    work = tmp_path / "work"
    payload = work / "payload"
    payload.mkdir(parents=True)
    (payload / "Youtube_abc.mp4").write_bytes(b"published candidate")
    source = {
        "id": "abc",
        "kind": "video",
        "url": "https://example.com/a",
        "profile": Profile(kind="video").model_dump_json(),
    }
    manifest = {"python": sys.executable, "ffmpeg": "f", "node": "n"}
    command = build_command(source, manifest, work, None)
    assert "--embed-thumbnail" in command


def test_structured_metadata_roundtrip():
    from downloader_tools.engine_metadata import completion

    event = completion(
        {
            "data": {
                "node": {"id": "123", "title": '作品"名', "user": {"profile": {"name": "投稿者"}}}
            }
        },
        "example",
        "https://example.com/post",
        "a" * 64,
    )
    event = json.loads(json.dumps(event))
    assert event["id"] == "123"
    assert event["title"] == '作品"名'
    assert event["uploader"] == "投稿者"


def test_source_host_requires_handshake_and_stops_when_parent_pipe_closes(tmp_path):
    from downloader_tools.paths import atomic_json

    config = tmp_path / "run.json"
    atomic_json(
        config,
        {
            "source_id": "s",
            "attempt_id": "a",
            "run_token": "token",
            "owner_path": str(tmp_path / "owner.json"),
            "receipts": str(tmp_path / "receipts"),
            "command": [sys.executable, "-c", "import time; time.sleep(60)"],
            "log_path": str(tmp_path / "log.txt"),
        },
    )
    host = subprocess.Popen(
        [sys.executable, "-m", "downloader_tools.source_host", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert host.stdout and host.stdin
    ready = json.loads(host.stdout.readline())
    assert ready["type"] == "ready" and ready["run_token"] == "token"
    import psutil

    owner = json.loads((tmp_path / "owner.json").read_text())
    assert owner["pid"] == ready["owner"]["pid"]
    assert psutil.Process(owner["pid"]).is_running()
    host.stdin.write(b'{"action":"start"}\n')
    host.stdin.flush()
    host.stdin.close()
    assert host.wait(timeout=12) == 0
    result = json.loads((tmp_path / "receipts/exit.json").read_text())
    assert result["stopped"]


def test_scheduling_does_not_starve_other_provider():
    from downloader_tools.scheduler import select_next

    queue = [
        {"id": "a", "batch_id": "b1", "kind": "video", "provider": "youtube"},
        {"id": "b", "batch_id": "b1", "kind": "gallery", "provider": "pixiv"},
        {"id": "c", "batch_id": "b2", "kind": "gallery", "provider": "reddit"},
    ]
    active = [{"kind": "video", "provider": "youtube"}]
    settings = {"total_slots": 2, "video_slots": 1, "gallery_slots": 1}
    assert select_next(queue, active, settings, "b1")["id"] == "c"
