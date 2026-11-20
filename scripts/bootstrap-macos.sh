#!/bin/sh
# Apple Silicon用。配布物とuvのキャッシュはクローン内だけに置く。
set -eu
test "$(uname -sm)" = "Darwin arm64" || { echo 'Apple Silicon Macが必要です。' >&2; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$ROOT"
export UV_CACHE_DIR="$ROOT/.cache/uv" UV_PYTHON_INSTALL_DIR="$ROOT/runtime/python"
export UV_PYTHON_BIN_DIR="$ROOT/runtime/bin" UV_NO_CONFIG=1
if [ "${DT_OFFLINE:-0}" = 1 ]; then export UV_OFFLINE=1; fi
mkdir -p runtime/media runtime/build-tools .cache/distributions
fetch() {
  url=$1
  archive="$ROOT/.cache/distributions/$2-${url##*/}"
  if [ ! -f "$archive" ]; then
    [ "${DT_OFFLINE:-0}" != 1 ] || { echo "オフライン: $archive がありません" >&2; exit 1; }
    curl -fsSL --retry 2 "$url" -o "$archive.part"
    printf '%s  %s\n' "$2" "$archive.part" | shasum -a 256 -c -
    mv "$archive.part" "$archive"
  fi
  printf '%s  %s\n' "$2" "$archive" | shasum -a 256 -c -
}
fetch https://github.com/astral-sh/uv/releases/download/0.13.9/uv-aarch64-apple-darwin.tar.gz 5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca
tar -xzOf "$archive" uv-aarch64-apple-darwin/uv > runtime/uv.new
chmod 755 runtime/uv.new && mv runtime/uv.new runtime/uv
fetch https://nodejs.org/dist/v24.22.3/node-v24.22.3-darwin-arm64.tar.gz ff6e17d4874b76d272212b908cc020d6e815fba5a6c7eaaa06add725840307fd
mkdir -p runtime/build-tools/node/bin
tar -xzOf "$archive" node-v24.22.3-darwin-arm64/bin/node > runtime/build-tools/node/bin/node.new
chmod 755 runtime/build-tools/node/bin/node.new && mv runtime/build-tools/node/bin/node.new runtime/build-tools/node/bin/node
if ! (cd runtime/media && shasum -a 256 -c ffmpeg-9.1.sha256 >/dev/null 2>&1); then
  (
    # configure/makeには空白なしの作業パスを渡し、システムのライブラリ検索に依存しない。
    set -eu
    build=$(mktemp -d /private/tmp/downloadertools-build.XXXXXXXX)
    trap 'rm -rf "$build"' EXIT
    unpack() {
      fetch "$2" "$3"
      mkdir "$build/$1"
      tar -xf "$archive" -C "$build/$1" --strip-components=1
    }
    unpack pkgconf https://distfiles.ariadne.space/pkgconf/pkgconf-2.5.1.tar.xz cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243
    unpack x264 https://codeload.github.com/mirror/x264/tar.gz/31e19f92f00c7003fa115047ce50978bc98c3a0d d053c9d86988d6bc78237ca5205865c5ddf99c98ef4cd9927eec8f6d388f6dd9
    unpack lame https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e
    unpack ffmpeg https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz 1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433
    prefix="$build/prefix"
    export PATH="/usr/bin:/bin:/usr/sbin:/sbin" PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
    (cd "$build/pkgconf" && ./configure --prefix="$prefix" --disable-shared && make -j4 && make install)
    (cd "$build/x264" && ./configure --prefix="$prefix" --enable-static --disable-cli && make -j4 && make install)
    (cd "$build/lame" && ./configure --build=arm-apple-darwin --prefix="$prefix" --disable-shared --disable-frontend && make -j4 && make install)
    (cd "$build/ffmpeg" && ./configure --disable-autodetect --disable-debug --disable-doc --disable-ffplay \
      --enable-gpl --enable-libx264 --enable-libmp3lame --enable-zlib --enable-securetransport \
      --pkg-config="$prefix/bin/pkgconf" --extra-cflags="-I$prefix/include" --extra-ldflags="-L$prefix/lib" && make -j4 ffmpeg ffprobe)
    for tool in ffmpeg ffprobe; do
      install -m 755 "$build/ffmpeg/$tool" "runtime/media/$tool.new"
      mv "runtime/media/$tool.new" "runtime/media/$tool"
    done
    (cd runtime/media && shasum -a 256 ffmpeg ffprobe > ffmpeg-9.1.sha256)
  )
fi
version=$(cat .python-version)
base="$UV_PYTHON_INSTALL_DIR/cpython-$version-macos-aarch64-none/bin/python3"
if [ ! -x "$base" ]; then runtime/uv python install "$version" --no-bin; fi
export UV_PYTHON="$base"
"$base" - <<'PY'
import fcntl, os, subprocess
from pathlib import Path

root = Path.cwd()
instance = root / "data/instance"
instance.mkdir(parents=True, exist_ok=True, mode=0o700)
with (instance / "app.lock").open("a+b") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("アプリを終了してからセットアップしてください。") from None
    stamp = root / "runtime/environment-root.txt"
    moving = stamp.exists() and stamp.read_text().strip() != str(root)
    if moving:
        subprocess.run(["runtime/uv", "venv", "--clear", "--python", os.environ["UV_PYTHON"], ".venv"], check=True)
    subprocess.run(["runtime/uv", "sync", "--locked", "--all-groups"], check=True)
    if moving:
        subprocess.run([".venv/bin/python", "scripts/relocate.py"], check=True)
    subprocess.run([".venv/bin/python", "scripts/build_toolset.py"], check=True)
    stamp.write_text(str(root))
PY
