"""隔離したtoolsetで実行するyt-dlpエントリーポイント。"""

import argparse
import os
import sys
from pathlib import Path

from yt_dlp import YoutubeDL, parse_options
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.embedthumbnail import EmbedThumbnailPP
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor, resolve_mapping

if not __package__:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from downloader_tools.engine_metadata import completion, video_identity
else:
    from .engine_metadata import completion, video_identity


class StorageNamePP(PostProcessor):
    def run(self, info):
        info["dt_storage"] = video_identity(info)
        return [], info


class CompletionPP(PostProcessor):
    def run(self, info):
        event = completion(
            info,
            info.get("extractor_key", ""),
            info.get("webpage_url", ""),
            info["dt_storage"],
        )
        event["path"] = info["filepath"]
        info["dt_completion"] = event
        return [], info


class MP4CompatiblePP(PostProcessor):
    def __init__(self, downloader, height):
        super().__init__(downloader)
        self.downloader = downloader
        self.ffmpeg = FFmpegPostProcessor(downloader)
        self.height = height

    def run(self, info):
        path = Path(info["filepath"])
        if path.stat().st_nlink > 1:
            return [], info
        streams = self.ffmpeg.get_metadata_object(str(path))["streams"]
        video = next(
            (
                stream
                for stream in streams
                if stream.get("codec_type") == "video"
                and not stream.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        recode_video = video and (
            video["codec_name"] != "h264" or video.get("height", 0) > self.height
        )
        recode_audio = any(stream["codec_name"] != "aac" for stream in audio)
        if path.suffix.lower() == ".mp4" and not recode_video and not recode_audio:
            return [], info
        target = path.with_suffix(".mp4")
        temporary = path.with_suffix(".compatible.mp4")
        options = [
            "-map",
            f"0:{video['index']}" if video else "0:v?",
            "-map",
            "0:a?",
            "-c:v",
            "libx264" if recode_video else "copy",
            "-c:a",
            "aac" if recode_audio else "copy",
            "-movflags",
            "+faststart",
        ]
        if recode_video:
            options += ["-pix_fmt", "yuv420p"]
            if video and video.get("height", 0) > self.height:
                options += ["-vf", f"scale=-2:{self.height // 2 * 2}"]
        self.downloader.to_screen(
            "MP4互換形式へ変換します"
            if recode_video or recode_audio
            else "コーデックを保持してMP4へ詰め替えます"
        )
        self.ffmpeg.run_ffmpeg(str(path), str(temporary), options)
        os.replace(temporary, target)
        info["filepath"], info["ext"] = str(target), "mp4"
        return [str(path)] if path != target else [], info


class RemuxResumePP(PostProcessor):
    def __init__(self, downloader, mapping):
        super().__init__(downloader)
        self.downloader = downloader
        self.mapping = mapping

    def run(self, info):
        # 条件付きremuxではyt-dlpが最終拡張子を推定できず、再取得・再変換してしまう。
        target, _ = resolve_mapping(info["ext"], self.mapping)
        self.downloader.params["final_ext"] = target or info["ext"]
        return [], info


class SafeEmbedThumbnailPP(PostProcessor):
    def __init__(self, downloader):
        super().__init__(downloader)
        self.embed = EmbedThumbnailPP(downloader)

    def run(self, info):
        # 公開ファイルと同じ実体の候補だけを除外し、再開後の新規項目には埋め込む。
        if os.stat(info["filepath"]).st_nlink > 1:
            return [
                thumb["filepath"] for thumb in info.get("thumbnails", []) if thumb.get("filepath")
            ], info
        if info["ext"] not in {
            "mp3",
            "mkv",
            "mka",
            "ogg",
            "opus",
            "flac",
            "m4a",
            "mp4",
            "m4v",
            "mov",
        }:
            self.report_warning("この形式はサムネイル埋め込み非対応のため、メディアのみ保存します")
            return [
                thumb["filepath"] for thumb in info.get("thumbnails", []) if thumb.get("filepath")
            ], info
        return self.embed.run(info)


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dt-mp4-height", type=int)
    args, remaining = parser.parse_known_args(sys.argv[1:])
    _, _, urls, options = parse_options(remaining)
    if args.dt_mp4_height:
        options["final_ext"] = "mp4"
    options["postprocessors"] = [
        pp for pp in options.get("postprocessors", []) if pp["key"] != "EmbedThumbnail"
    ]
    with YoutubeDL(options) as downloader:
        downloader.add_post_processor(StorageNamePP(downloader), when="video")
        for processor in options.get("postprocessors", []):
            if processor["key"] == "FFmpegVideoRemuxer":
                downloader.add_post_processor(
                    RemuxResumePP(downloader, processor["preferedformat"]), when="before_dl"
                )
        if args.dt_mp4_height:
            downloader.add_post_processor(MP4CompatiblePP(downloader, args.dt_mp4_height))
        downloader.add_post_processor(SafeEmbedThumbnailPP(downloader))
        # yt-dlpのprintもafter_moveのPPなので、完成通知をその直前に準備する。
        downloader.add_post_processor(CompletionPP(downloader), when="post_process")
        return downloader.download(urls)


if __name__ == "__main__":
    sys.exit(main())
