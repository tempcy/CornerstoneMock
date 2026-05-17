# 本地一键启动 Bridge + Web（不依赖 Scripts 是否在 PATH）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m cornerstone_web.dev_web @args
exit $LASTEXITCODE
