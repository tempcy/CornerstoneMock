$ErrorActionPreference = "Continue"
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    if ($_.OwningProcess) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*run*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
schtasks /Run /TN "\CornerstoneAgent-Orchestrator"
Start-Sleep -Seconds 8
curl.exe -s -m 8 http://127.0.0.1:8090/health
Write-Output ""
