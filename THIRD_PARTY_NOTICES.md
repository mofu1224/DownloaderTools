# 同梱・取得する外部ソフトウェアの表示

本リポジトリは、アプリ本体のほかに外部ソフトウェアを同梱または検証済みの固定版として取得します。
再配布する場合は、各ソフトウェアのライセンス本文と著作権表示を確認し、同梱して条件を満たしてください。
本ファイルは一覧であり、ライセンス本文の代替ではありません。正確な条件は各配布元の表示を正とします。

## 同梱物（`runtime/` 配下）

| 構成要素 | 同梱先 | ライセンス（配布元表示に基づく） | 配布元 |
| --- | --- | --- | --- |
| Python 3.14.10 | `runtime/python/` | PSF License Agreement | https://www.python.org/ |
| uv 0.13.9 | `runtime/` | Apache-2.0 または MIT | https://github.com/astral-sh/uv |
| Node.js v24.22.3 | `runtime/build-tools/`, 各toolset `bin/` | MIT | https://nodejs.org/ |
| FFmpeg / FFprobe 9.1 | `runtime/media/`, 各toolset `bin/` | GPL（Windows: gyan.devのessentialsビルド、Mac: libx264有効） | https://ffmpeg.org/ |
| SQLite 3.54.1（アプリのDB） | Windows: `runtime/python/.../DLLs/`、Mac: `.venv/` のpysqlite3 0.6.2 | SQLiteはPublic Domain | https://sqlite.org/ |
| yt-dlp 2026.08.19 | `runtime/toolsets/*/python/` | Unlicense | https://github.com/yt-dlp/yt-dlp |
| gallery-dl 1.32.16 | `runtime/toolsets/*/python/` | GPL-2.0 | https://github.com/mikf/gallery-dl |

FFmpegとgallery-dlはGPLが適用されます。GPLの配布物を再配布する場合は、対応するソースの提供などの
GPL条件を満たす必要があります。各同梱物に付属するライセンス本文（`LICENSE`等）を必ず確認してください。

Macではビルド時にpkgconf 2.5.1を使い、FFmpegにx264（GPL）とLAME 3.100（LGPL）を静的リンクします。使用したソースと検証値は `scripts/bootstrap-macos.sh` で確認できます。

## 取得する固定版の依存（`uv.lock` / 各toolsetの `requirements.txt`）

バージョンとハッシュで固定し、PyPIから取得します。主なものは次のとおりです。

- FastAPI, Starlette, uvicorn, httpx, httpcore, anyio, h11, click, sniffio（MIT / BSD-3-Clause系）
- Jinja2（BSD-3-Clause）, pydantic, pydantic-core, annotated-types, typing-extensions（MIT）
- portalocker, psutil（BSD-3-Clause）
- Pillow（MIT-CMU / HPND）, tzdata（Apache-2.0）
- pywin32（PSF）, certifi（MPL-2.0）, idna（BSD-3-Clause）, urllib3（MIT）
- brotli（MIT）, mutagen（GPL-2.0）, pycryptodomex（public domain / BSD-2-Clause）, requests（Apache-2.0）, websockets（BSD-3-Clause）
- エンジン: yt-dlp（Unlicense）, gallery-dl（GPL-2.0）

正確なライセンスは、取得先の各パッケージに含まれる `METADATA` / `LICENSE` と配布元を確認してください。

## 開発・ビルド時のみ使用（配布物には実行時同梱しない）

- TypeScript（Apache-2.0）, Vite / Rollup / esbuild / PostCSS（MIT ほか）: フロント静的資産のビルド時のみ。

## 本リポジトリ自身

アプリ本体は [MITライセンス](LICENSE) です。上記の外部ソフトウェアにはそれぞれのライセンスが適用されます。

配布はリポジトリクローンを前提とし、ソースとビルド済みUIをリポジトリで管理します。`runtime/`やインストール済みPythonパッケージは追跡せず、初回起動時に固定版を取得します。配布用ZIPやバイナリ同梱セットは作成しません。取得後の各ソフトウェアの利用・改変・再配布には、それぞれのライセンス条件が適用されます。
