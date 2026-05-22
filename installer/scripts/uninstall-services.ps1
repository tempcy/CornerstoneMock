param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = "SilentlyContinue"
$nssm = Join-Path $AppDir "tools\nssm.exe"
if (-not (Test-Path $nssm)) { return }

foreach ($name in @("CornerstoneBridge", "CornerstoneWeb")) {
    & $nssm stop $name confirm 2>$null
    & $nssm remove $name confirm 2>$null
}
