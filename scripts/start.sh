#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
if [ ! -x "$ROOT/.venv/bin/python" ] || [ "$(cat "$ROOT/runtime/environment-root.txt" 2>/dev/null || :)" != "$ROOT" ]; then
  /bin/sh "$ROOT/scripts/bootstrap-macos.sh"
fi
exec "$ROOT/.venv/bin/python" -m downloader_tools "$@"
