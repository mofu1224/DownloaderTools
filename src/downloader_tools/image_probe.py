import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def probe_archive(path):
    import zipfile

    try:
        if not zipfile.is_zipfile(path):
            return 3 if Path(path).suffix.lower() == ".zip" else 2
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            # 展開せずCRCを検証する。過大な展開量と暗号化ZIPは公開しない。
            if (
                not entries
                or len(entries) > 10000
                or sum(entry.file_size for entry in entries) > 4 * 1024**3
                or any(entry.flag_bits & 1 for entry in entries)
            ):
                return 3
            return 3 if archive.testzip() else 0
    except OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError:
        return 3


def main():
    try:
        with Image.open(sys.argv[1]) as image:
            if image.format == "MPEG":
                return 2
            image.verify()
    except UnidentifiedImageError:
        if "--images-only" in sys.argv[2:]:
            import zipfile

            if zipfile.is_zipfile(sys.argv[1]):
                return 4
        if "--allow-archives" in sys.argv[2:]:
            archive_result = probe_archive(sys.argv[1])
            if archive_result != 2:
                return archive_result
        return 1 if Path(sys.argv[1]).suffix.lower() in Image.registered_extensions() else 2
    except OSError, ValueError, SyntaxError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
