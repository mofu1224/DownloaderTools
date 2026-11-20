import json
from pathlib import Path

from .domain import Profile
from .paths import atomic_json, root_path


def absolute(path: str) -> str:
    return str((root_path() / path).resolve())


def build_command(source: dict, manifest: dict, work: Path, cookie: Path | None) -> list[str]:
    profile = Profile.model_validate_json(source["profile"])
    python = absolute(manifest["python"])
    payload = work / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    if source["kind"] == "video":
        command = [
            python,
            "-I",
            "-X",
            "utf8",
            str(Path(__file__).with_name("video_engine.py")),
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-colors",
            "--no-quiet",
            "--output-na-placeholder",
            "null",
            "--newline",
            "--no-overwrites",
            "--continue",
            "--no-cache-dir",
            "--retries",
            "2",
            "--fragment-retries",
            "2",
            "--extractor-retries",
            "2",
            "--file-access-retries",
            "2",
            "--socket-timeout",
            "30",
            "--retry-sleep",
            "exp=1:20",
            "--concurrent-fragments",
            str(profile.fragments),
            "--ffmpeg-location",
            str(Path(absolute(manifest["ffmpeg"])).parent),
            "--js-runtimes",
            "node:" + absolute(manifest["node"]),
            "--yes-playlist" if profile.playlist else "--no-playlist",
            "--no-simulate",
            "--progress",
            "--progress-delta",
            "0.25",
            "--progress-template",
            "download:DT_PROGRESS\t%(progress)j",
            "--print",
            "after_move:DT_ITEM\t%(dt_completion)j",
            "-o",
            str(payload / "%(dt_storage)s.%(ext)s"),
            "--windows-filenames",
        ]
        if profile.container == "mp4":
            command += [
                "-f",
                f"bv*[height<=?{profile.height}]+ba/b[height<=?{profile.height}]",
                "--merge-output-format",
                "mkv",
                "--dt-mp4-height",
                str(profile.height),
            ]
        elif profile.container in ("audio", "mp3"):
            command += ["-f", "bestaudio/best", "-x"]
            if profile.container == "mp3":
                command += ["--audio-format", "mp3", "--audio-quality", "0"]
        else:
            command += ["-f", f"bv*[height<=?{profile.height}]+ba/b[height<=?{profile.height}]"]
            if profile.container == "mkv":
                command += ["--merge-output-format", "mkv", "--remux-video", "mkv"]
            else:
                command += ["--remux-video", "webm>mkv"]
        command += ["--embed-thumbnail", "--convert-thumbnails", "png"]
        if cookie:
            command += ["--cookies", str(cookie)]
    else:
        configuration = {
            "extractor": {
                "base-directory": str(payload),
                "retries": 2,
                "timeout": 30,
                "skip": True,
                "postprocessors": [],
                "path-restrict": "windows+",
                "directory": [],
                "filename": "{dt_storage}.{extension}",
                "parent-directory": False,
            },
            "directory": [],
            "filename": "{dt_storage}.{extension}",
            "downloader": {
                "ytdl": {
                    "module": "yt_dlp",
                    "raw-options": {
                        "ffmpeg_location": str(Path(absolute(manifest["ffmpeg"])).parent),
                        "js_runtimes": {"node": {"path": absolute(manifest["node"])}},
                        "retries": 2,
                        "fragment_retries": 2,
                        "socket_timeout": 30,
                    },
                },
            },
            "output": {"mode": "null", "shorten": False, "ansi": False},
            "cache": {"file": str(work / "gallery-cache.sqlite3")},
        }
        if profile.media_policy == "images":
            configuration["extractor"]["dt-images-only"] = True
        config_path = work / "gallery-config.json"
        atomic_json(config_path, configuration)
        command = [
            python,
            "-I",
            "-X",
            "utf8",
            str(Path(__file__).with_name("gallery_engine.py")),
            "--config-ignore",
            "--config",
            str(config_path),
            "--no-input",
            "--no-colors",
            "--retries",
            "2",
            "--http-timeout",
            "30",
        ]
        if cookie:
            command += ["--cookies", str(cookie)]
    return command + ["--", source["url"]]


def parse_line(line: str) -> dict | None:
    if line.startswith("DT_ITEM\t"):
        result = json.loads(line.split("\t", 1)[1])
        if not isinstance(result, dict) or not isinstance(result.get("path"), str):
            raise ValueError("ENGINE_PROTOCOL_INVALID")
        result["type"] = "item_ready"
        return result
    if line.startswith("DT_PROGRESS\t"):
        result = json.loads(line.split("\t", 1)[1])
        if not isinstance(result, dict):
            raise ValueError("ENGINE_PROTOCOL_INVALID")
        return {
            "type": "progress",
            "downloaded_bytes": result.get("downloaded_bytes"),
            "total_bytes": result.get("total_bytes") or result.get("total_bytes_estimate"),
            "speed": result.get("speed"),
            "eta": result.get("eta"),
            "phase": result.get("status", "downloading"),
        }
    return None
