$ErrorActionPreference = "Stop"
$root = "C:\CornerstoneMock\CornerstoneAgent"
$zip = "C:\CornerstoneMock\csagent-0.3.2.zip"
$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"

Write-Output "===EXPAND==="
Expand-Archive -Path $zip -DestinationPath $root -Force
Get-Content (Join-Path $root "src\cornerstone_agent\__init__.py")

Write-Output "===MERGE==="
& $py (Join-Path $root "scripts\_merge_timeseries_config.py")

Write-Output "===STOP==="
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    if ($_.OwningProcess) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*run*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Write-Output "===TASK==="
schtasks /Run /TN "\CornerstoneAgent-Orchestrator"
Start-Sleep -Seconds 8

Write-Output "===HEALTH==="
$h = & curl.exe -s -m 8 http://127.0.0.1:8090/health 2>$null
if ($h) { Write-Output $h } else { Write-Output "NO_RESPONSE" }
