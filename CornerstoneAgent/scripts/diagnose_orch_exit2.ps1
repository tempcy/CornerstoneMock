$root = "C:\CornerstoneMock\CornerstoneAgent"
Write-Output "===TIMING==="
Get-Item (Join-Path $root "orch.out.log"), (Join-Path $root "orch.err.log") |
  Format-List FullName, Length, CreationTime, LastWriteTime

Write-Output "===BOOT==="
$os = Get-CimInstance Win32_OperatingSystem
"LastBootUpTime=$($os.LastBootUpTime)"
"LocalDateTime=$($os.LocalDateTime)"

Write-Output "===SECURITY_4689_PYTHON==="
# Process Termination events if auditing enabled
Get-WinEvent -FilterHashtable @{
  LogName = "Security"
  Id = 4689
  StartTime = (Get-Date "2026-08-17 08:55:00")
  EndTime = (Get-Date "2026-08-17 09:15:00")
} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match "python|cornerstone" } |
  Select-Object -First 10 TimeCreated, @{n="Msg";e={$_.Message.Substring(0,[Math]::Min(500,$_.Message.Length))}} |
  Format-List

Write-Output "===APP_ALL_NEAR==="
Get-WinEvent -FilterHashtable @{
  LogName = "Application"
  StartTime = (Get-Date "2026-08-17 08:50:00")
  EndTime = (Get-Date "2026-08-17 09:20:00")
} -ErrorAction SilentlyContinue |
  Select-Object -First 30 TimeCreated, Id, ProviderName, @{n="Msg";e={($_.Message -replace '\s+',' ').Substring(0,[Math]::Min(220,($_.Message -replace '\s+',' ').Length))}} |
  Format-List

Write-Output "===OPENSSH_JOB==="
# Show whether sshd uses job objects (documented Windows OpenSSH behavior)
Get-Service sshd | Format-List Status, StartType
Get-Content "C:\ProgramData\ssh\sshd_config" -ErrorAction SilentlyContinue |
  Select-String -Pattern "Job|Subsystem|Match|ForceCommand" 

Write-Output "===PREV_START_HINT==="
# Any leftover start scripts / scheduled tasks with broader search
schtasks /Query /FO CSV /V 2>$null | findstr /i "python Cornerstone cornerstone Agent orch 8090"
Get-ChildItem "$env:USERPROFILE\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup" -ErrorAction SilentlyContinue
Get-ChildItem "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup" -ErrorAction SilentlyContinue
