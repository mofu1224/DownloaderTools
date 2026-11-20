param(
    [switch]$Offline,
    [switch]$BuildFrontend
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$offline = $Offline -or ($env:DT_OFFLINE -eq '1')
$uvOffline = @()
if ($offline) { $uvOffline = @('--offline') }
if ($offline) { $env:DT_OFFLINE = '1' } else { $env:DT_OFFLINE = '0' }
# 日本語を含むパス・ファイル名を子プロセスでも安全に扱う。
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:UV_CACHE_DIR = Join-Path $root '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root 'runtime/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $root 'runtime/bin'
$env:npm_config_cache = Join-Path $root '.cache/npm'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root '.cache/playwright'
$lockFile = Join-Path $root 'data/instance/app.lock'
if (Test-Path -LiteralPath $lockFile) {
    try { $guard = [IO.File]::Open($lockFile, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None); $guard.Dispose() }
    catch { throw '起動中のアプリを終了してからセットアップしてください。' }
}
function Run-Native([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "実行失敗: $exe" }
}
$runtime = Join-Path $root 'runtime'
[IO.Directory]::CreateDirectory($runtime) | Out-Null
$uv = Join-Path $runtime 'uv.exe'
if (!(Test-Path -LiteralPath $uv)) {
    if ($offline) { throw 'オフラインではuvを取得できません。ネットワーク接続後に再実行してください。' }
    $url = 'https://github.com/astral-sh/uv/releases/download/0.11.17/uv-x86_64-pc-windows-msvc.zip'
    $archive = Join-Path $runtime 'uv.zip'
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri $url -OutFile $archive
    $expected = '35fc29e03e62f3cda769bc12773f3cb70ce305d0d36c0d8bd0c117dd0b3fcd14'
    $hash = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($archive)
    try { $actual = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLower() }
    finally { $stream.Dispose(); $hash.Dispose() }
    if ($actual -ne $expected) { throw 'uvのハッシュが不一致です' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $entries = @($zip.Entries | Where-Object { $_.Name -eq 'uv.exe' })
        if ($entries.Count -ne 1) { throw 'uv.exe is missing from the verified archive' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $uv, $true)
    } finally { $zip.Dispose() }
    Remove-Item -LiteralPath $archive
}
$python = Join-Path $runtime 'python/cpython-3.14.5-windows-x86_64-none/python.exe'
if (Test-Path -LiteralPath $python) {
    # 同梱のPythonを使い、uvによる導入や版リンクの作成を行わない。
    $env:UV_PYTHON = $python
} else {
    Run-Native $uv (@('python','install','3.14.5','--no-bin','--no-registry') + $uvOffline)
}
Push-Location $root
try {
    $prepare = @('scripts/prepare_runtime.py')
    if ($offline) { $prepare += '--offline' }
    Run-Native $python $prepare
    $stamp = Join-Path $runtime 'environment-root.txt'
    if ((Test-Path -LiteralPath $stamp) -and [IO.File]::ReadAllText($stamp).Trim() -ne $root) {
        # 移設で無効になった仮想環境だけを、lockから作り直す。
        if (Test-Path -LiteralPath (Join-Path $root '.venv')) { Remove-Item -LiteralPath (Join-Path $root '.venv') -Recurse -Force }
        Run-Native $python @('scripts/relocate.py')
    }
    Run-Native $uv (@('sync','--locked','--all-groups') + $uvOffline)
    $localPython = Join-Path $root '.venv/Scripts/python.exe'
    Run-Native $localPython @('scripts/build_toolset.py')
    $static = Join-Path $root 'src/downloader_tools/static/app.js'
    if ($BuildFrontend -or !(Test-Path -LiteralPath $static)) {
        if ($offline) { throw 'オフラインではフロントをビルドできません（静的資産がありません）。' }
        $node = Join-Path $runtime 'build-tools/node-v24.14.1-win-x64'
        $env:PATH = "$node;$env:PATH"
        Run-Native (Join-Path $node 'npm.cmd') @('ci','--prefix','frontend','--ignore-scripts','--no-audit','--no-fund')
        Run-Native (Join-Path $node 'npm.cmd') @('run','build','--prefix','frontend')
    } else {
        Write-Host 'ビルド済みのフロント静的資産を使用します（再ビルドは -BuildFrontend）。'
    }
    [IO.File]::WriteAllText($stamp, $root, [Text.UTF8Encoding]::new($false))
} finally { Pop-Location }
Write-Host 'セットアップ完了。Start-DownloaderTools.batで起動できます。'
