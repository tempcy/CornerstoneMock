$logDir = "C:\CornerstoneMock\CornerstoneAgent\logs"
Write-Output "===LOGDIR==="
if (Test-Path $logDir) {
  Get-ChildItem $logDir | Format-Table Name, Length, LastWriteTime -AutoSize
  Write-Output "===OUT_TAIL==="
  $out = Join-Path $logDir "orch.out.log"
  if (Test-Path $out) { Get-Content $out -Tail 50 }
  Write-Output "===ERR_TAIL==="
  $err = Join-Path $logDir "orch.err.log"
  if (Test-Path $err) {
    $t = Get-Content $err -Tail 50
    if ($t) { $t } else { "(empty)" }
  }
} else { "(no logs dir)" }

Write-Output "===DWM_SESSION==="
Get-WinEvent -FilterHashtable @{
  LogName = "Application"
  ProviderName = "Desktop Window Manager"
  StartTime = (Get-Date "2026-08-17 08:00:00")
} -ErrorAction SilentlyContinue |
  Select-Object -First 10 TimeCreated, Id, Message | Format-List
