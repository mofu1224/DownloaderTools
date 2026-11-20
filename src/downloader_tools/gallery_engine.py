"""gallery-dlの抽出・巡回を保ち、一時パスと完成通知だけを共通化する。"""

import collections
import json
import re
import sys
import time
from pathlib import Path

import gallery_dl
from gallery_dl import job
from gallery_dl.downloader.ytdl import YoutubeDLDownloader

# -Iで隔離したtoolsetから、このアダプターと標準ライブラリ製の共通処理だけを読む。
if not __package__:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from downloader_tools.engine_metadata import (
        completion,
        gallery_identity,
        identity_hash,
        video_identity,
    )
    from downloader_tools.media_policy import IMAGES_ONLY, NON_IMAGE_EXTENSIONS
else:
    from .engine_metadata import completion, gallery_identity, identity_hash, video_identity
    from .media_policy import IMAGES_ONLY, NON_IMAGE_EXTENSIONS


class GalleryVideoDownloader(YoutubeDLDownloader):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

    def _download_playlist(self, ytdl_instance, pathfmt, info_dict):
        original = pathfmt.kwdict
        succeeded = False
        failed = False
        try:
            for entry in info_dict["entries"]:
                if not entry:
                    continue
                pathfmt.kwdict = {
                    **original,
                    **entry,
                    "dt_storage": identity_hash([original["dt_storage"], video_identity(entry)]),
                }
                if self.rate_dyn is not None:
                    ytdl_instance.params["ratelimit"] = self.rate_dyn()
                if "entries" in entry:
                    success = self._download_playlist(ytdl_instance, pathfmt, entry)
                else:
                    success = self._download_video(ytdl_instance, pathfmt, entry)
                    if success:
                        if pathfmt.temppath:
                            pathfmt.finalize()
                        self.parent.emit_item(pathfmt)
                succeeded |= success
                failed |= not success
        finally:
            pathfmt.kwdict = original
            pathfmt.temppath = ""
            original["dt_playlist_done"] = True
        return succeeded and not failed


class StructuredDownloadJob(job.DownloadJob):
    def __init__(self, url, parent=None):
        super().__init__(url, parent)
        self.out = StructuredOutput()

    def initialize(self, kwdict=None):
        super().initialize(kwdict)
        self.hooks = collections.defaultdict(list, self.hooks or {})
        self.register_hooks({"after": self.emit_item, "skip": self.emit_item})

    def handle_url(self, url, kwdict):
        kwdict.pop("dt_playlist_done", None)
        extension = kwdict.get("extension")
        if extension and (
            not isinstance(extension, str) or not re.fullmatch(r"[a-zA-Z0-9]{1,16}", extension)
        ):
            # 未確定・不正な拡張子はHTTP応答からgallery-dl自身に判定させる。
            kwdict["extension"] = ""
        kwdict["dt_storage"] = gallery_identity(
            self.extractor.category, self.extractor.url, url, kwdict
        )
        if self.extractor.config("dt-images-only", False) and (
            str(extension).lower() in NON_IMAGE_EXTENSIONS or url.startswith("ytdl:")
        ):
            event = completion(
                kwdict, self.extractor.category, self.extractor.url, kwdict["dt_storage"]
            )
            event.update(path="", policy_skip=IMAGES_ONLY)
            print("DT_ITEM\t" + json.dumps(event, ensure_ascii=True), flush=True)
            return
        return super().handle_url(url, kwdict)

    def get_downloader(self, scheme):
        if scheme == "ytdl":
            if scheme not in self.downloaders:
                self.downloaders[scheme] = GalleryVideoDownloader(self)
            return self.downloaders[scheme]
        return super().get_downloader(scheme)

    def emit_item(self, pathfmt):
        if pathfmt.kwdict.get("dt_playlist_done"):
            return
        event = completion(
            pathfmt.kwdict,
            self.extractor.category,
            self.extractor.url,
            pathfmt.kwdict["dt_storage"],
        )
        event["path"] = pathfmt.path
        print("DT_ITEM\t" + json.dumps(event, ensure_ascii=True), flush=True)


class StructuredOutput:
    def __init__(self):
        self.last_progress = 0.0

    def start(self, path):
        self.last_progress = 0.0
        print("gallery: download_start", flush=True)

    def skip(self, path):
        print("gallery: file_already_exists", flush=True)

    def success(self, path):
        print("gallery: download_finished", flush=True)

    def progress(self, bytes_total, bytes_downloaded, bytes_per_second):
        now = time.monotonic()
        if now - self.last_progress < 1:
            return
        self.last_progress = now
        print(
            "DT_PROGRESS\t"
            + json.dumps(
                {
                    "status": "downloading",
                    "total_bytes": bytes_total,
                    "downloaded_bytes": bytes_downloaded,
                    "speed": bytes_per_second,
                }
            ),
            flush=True,
        )


def main():
    # CLIの設定・Cookie・フィルター・子extractor・終了コードの処理は上流へ委ねる。
    job.DownloadJob = StructuredDownloadJob
    return gallery_dl.main()


if __name__ == "__main__":
    sys.exit(main())
