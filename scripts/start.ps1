param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArguments)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
$stamp = Join-Path $root 'runtime/environment-root.txt'
$rebuild = !(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $stamp)
if (!$rebuild) { $rebuild = [IO.File]::ReadAllText($stamp).Trim() -ne $root }
if ($rebuild) {
    $bootstrap = Join-Path $PSScriptRoot 'bootstrap.ps1'
    $bundled = Test-Path -LiteralPath (Join-Path $root 'runtime/python')
    $done = $false
    if ($bundled) {
        # 同梱済みランタイムとキャッシュだけで移設先を再構築できるか先に試す。
        try { & $bootstrap -Offline; $done = $true }
        catch { Write-Host ('オフライン準備に失敗したためオンラインで再試行します: ' + $_.Exception.Message) }
    }
    if (!$done) { & $bootstrap }
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $python -m downloader_tools @AppArguments
exit $LASTEXITCODE
