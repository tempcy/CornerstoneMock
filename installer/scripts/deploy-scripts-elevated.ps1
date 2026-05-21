# Copy installer scripts into Program Files (requires Administrator).
param(
    [string]$SourceDir = (Join-Path $PSScriptRoot "."),
    [string]$DestDir = "C:\Program Files\CornerstoneMock\scripts"
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $p = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host "[deploy] Re-launching elevated ..."
    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-SourceDir", $SourceDir,
        "-DestDir", $DestDir
    )
    $proc = Start-Process powershell.exe -ArgumentList $args -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

if (-not (Test-Path $DestDir)) {
    throw "Destination not found (install Cornerstone Mock first): $DestDir"
}

foreach ($name in @("CornerstoneBridge", "CornerstoneWeb")) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Stopped") {
        Write-Host "[deploy] Stopping $name ..."
        Stop-Service -Name $name -Force
        Start-Sleep -Seconds 2
    }
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
Copy-Item (Join-Path $SourceDir "*") $DestDir -Force
Write-Host "[deploy] Copied scripts to $DestDir"
Get-ChildItem $DestDir -Filter "*.ps1" | Select-Object Name, Length, LastWriteTime
