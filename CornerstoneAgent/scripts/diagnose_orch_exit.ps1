$root = "C:\CornerstoneMock\CornerstoneAgent"
$ErrorActionPreference = "Continue"

Write-Output "===FILES==="
Get-ChildItem $root -Filter "orch*" | Format-Table Name, Length, LastWriteTime -AutoSize
Get-ChildItem $root -Filter "*.log" | Format-Table Name, Length, LastWriteTime -AutoSize

Write-Output "===OUT_FULL==="
$out = Join-Path $root "orch.out.log"
if (Test-Path $out) { Get-Content $out -Raw } else { "(missing)" }

Write-Output "===ERR_FULL==="
$err = Join-Path $root "orch.err.log"
if (Test-Path $err) {
  $c = Get-Content $err -Raw
  if ([string]::IsNullOrWhiteSpace($c)) { "(empty)" } else { $c }
} else { "(missing)" }

Write-Output "===EVENT_APP_CRASH==="
Get-WinEvent -FilterHashtable @{
  LogName = "Application"
  StartTime = (Get-Date).AddHours(-12)
} -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Message -match "python|cornerstone|8090|Application Error|Windows Error Reporting" -or
    $_.Id -in 1000,1001,1002,1026
  } |
  Select-Object -First 25 TimeCreated, Id, ProviderName, LevelDisplayName, @{n="Msg";e={$_.Message.Substring(0,[Math]::Min(400,$_.Message.Length))}} |
  Format-List

Write-Output "===EVENT_SYSTEM_POWER==="
Get-WinEvent -FilterHashtable @{
  LogName = "System"
  StartTime = (Get-Date).AddHours(-12)
  Id = 41,1074,6006,6008,7001,7002
} -ErrorAction SilentlyContinue |
  Select-Object -First 15 TimeCreated, Id, ProviderName, @{n="Msg";e={$_.Message.Substring(0,[Math]::Min(250,$_.Message.Length))}} |
  Format-List

Write-Output "===WER_RECENT==="
$wer = "C:\ProgramData\Microsoft\Windows\WER\ReportArchive"
if (Test-Path $wer) {
  Get-ChildItem $wer -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 8 Name, LastWriteTime
}

Write-Output "===TASKS==="
schtasks /Query /FO LIST /V 2>$null | Select-String -Pattern "Cornerstone|Agent|8090|orch" -Context 0,2

Write-Output "===SESSION==="
quser query
Write-Output "uptime/boot:"
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
