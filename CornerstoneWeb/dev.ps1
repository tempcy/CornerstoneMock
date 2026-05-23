# 本地一键启动 Bridge + Web（不依赖 Scripts 是否在 PATH）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "python"
try {
    $resolved = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
    if ($resolved -and (Test-Path -LiteralPath $resolved)) { $py = $resolved }
} catch { }
& $py -m cornerstone_web.dev_web @args
exit $LASTEXITCODE
