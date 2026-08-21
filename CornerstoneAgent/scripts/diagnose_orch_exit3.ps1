Write-Output "===CMD==="
Get-Content "C:\Users\user\run_orch_2lg.cmd" -ErrorAction SilentlyContinue
Write-Output "===TASK==="
schtasks /Query /TN "CornerstoneAgent-Orchestrator" /V /FO LIST
Write-Output "===LOG_TIMES==="
Get-Item "C:\CornerstoneMock\CornerstoneAgent\orch.out.log" |
  Select-Object FullName, Length, CreationTime, LastWriteTime | Format-List
