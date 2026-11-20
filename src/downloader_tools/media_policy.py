NON_IMAGE_EXTENSIONS = frozenset(
    {
        "mp4",
        "mkv",
        "webm",
        "mov",
        "m4v",
        "avi",
        "flv",
        "ts",
        "mpg",
        "mpeg",
        "3gp",
        "mp3",
        "m4a",
        "aac",
        "ogg",
        "opus",
        "flac",
        "wav",
        "wma",
        "m3u8",
        "mpd",
        "ytdl",
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "bz2",
        "xz",
        "pdf",
        "txt",
        "json",
        "exe",
        "com",
        "bat",
        "cmd",
        "ps1",
        "py",
        "js",
        "lnk",
        "msi",
    }
)
IMAGES_ONLY = "MEDIA_POLICY_IMAGES_ONLY"


class MediaPolicySkip(ValueError):
    def __init__(self):
        super().__init__(IMAGES_ONLY)
