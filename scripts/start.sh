#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  printf '%s\n' 'macOS用ランタイムをセットアップしてください（README.mdを参照）。' >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" -m downloader_tools "$@"
