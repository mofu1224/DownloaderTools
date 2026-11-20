"""固定版エンジンのオフラインCLI・構造出力契約を検証する。"""

import json
import subprocess
import sys


def main():
    import gallery_dl.version
    import yt_dlp.version
    from gallery_dl import formatter

    template = (
        '{{"path":{_path.path!j},"category":{category!j},'
        '"id":{id|post_id|tweet_id|media_id!j},"asset":{num|index|filename!j}}}'
    )

    class Proxy:
        path = '/quoted"/日本語.png'

    result = json.loads(
        formatter.parse(template).format_map(
            {"_path": Proxy(), "category": "probe", "id": "1", "num": 2, "filename": "a"}
        )
    )
    assert result == {"path": '/quoted"/日本語.png', "category": "probe", "id": "1", "asset": 2}
    for module, options in (
        ("yt_dlp", ("--no-plugin-dirs", "--progress-template", "--print", "--ignore-config")),
        ("gallery_dl", ("--config-ignore", "--Print", "--no-input")),
    ):
        text = subprocess.check_output(
            [sys.executable, "-I", "-m", module, "--help"], timeout=30
        ).decode()
        if any(option not in text for option in options):
            raise RuntimeError("未対応のCLI契約: " + module)
    print(
        json.dumps(
            {"yt-dlp": yt_dlp.version.__version__, "gallery-dl": gallery_dl.version.__version__}
        )
    )


if __name__ == "__main__":
    main()
