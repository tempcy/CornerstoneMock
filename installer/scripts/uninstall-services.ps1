# 卸载：停止服务与进程 → 移除 NSSM 服务；保留 %APPDATA%\CornerstoneMock 下用户配置。
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [string]$ConfigDir = "",
    [string]$PreserveConfig = "1"
)

$ErrorActionPreference = "SilentlyContinue"

if (-not $ConfigDir) {
    $ConfigDir = Join-Path $env:APPDATA "CornerstoneMock"
}

function Write-UninstallLog {
    param([string]$Message)
    $logDir = Join-Path $ConfigDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -Path (Join-Path $logDir "uninstall.log") -Value $line -Encoding UTF8
}

Write-UninstallLog "uninstall start AppDir=$AppDir PreserveConfig=$PreserveConfig"

$processNames = @(
    "cornerstone-bridge",
    "cornerstone-web",
    "CornerstoneQueue",
    "cornerstone-cli"
)

foreach ($svcName in @("CornerstoneBridge", "CornerstoneWeb")) {
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-UninstallLog "Stop-Service $svcName"
        Stop-Service -Name $svcName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

$nssm = Join-Path $AppDir "tools\nssm.exe"
if (Test-Path $nssm) {
    foreach ($name in @("CornerstoneBridge", "CornerstoneWeb")) {
        Write-UninstallLog "nssm stop/remove $name"
        & $nssm stop $name confirm 2>$null
        Start-Sleep -Seconds 1
        & $nssm remove $name confirm 2>$null
    }
} else {
    Write-UninstallLog "nssm.exe not found under AppDir"
}

foreach ($procName in $processNames) {
    $procs = Get-Process -Name $procName -ErrorAction SilentlyContinue
    if ($procs) {
        Write-UninstallLog "Stop-Process $procName count=$($procs.Count)"
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1
foreach ($procName in $processNames) {
    if (Get-Process -Name $procName -ErrorAction SilentlyContinue) {
        Write-UninstallLog "WARN: process still running: $procName"
    }
}

if ($PreserveConfig -eq "1") {
    Write-UninstallLog "preserved config dir: $ConfigDir (json/queue not deleted by this script)"
} else {
    Write-UninstallLog "PreserveConfig=0 (Inno still does not remove Roaming config by default)"
}

Write-UninstallLog "uninstall done"
