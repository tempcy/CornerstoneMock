$ErrorActionPreference = "Continue"
$root = "C:\CornerstoneMock\CornerstoneAgent"
$py = "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
$task = "\CornerstoneAgent-Orchestrator"

Write-Output "===STOP_OLD==="
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    Write-Output "kill pid=$($_.OwningProcess)"
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*run*" } |
  ForEach-Object {
    Write-Output "kill python pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 2

Write-Output "===ENSURE_LOGDIR==="
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null

Write-Output "===RUN_TASK==="
$r = schtasks /Run /TN $task 2>&1
Write-Output $r
Start-Sleep -Seconds 5

Write-Output "===HEALTH1==="
$h1 = & curl.exe -s -m 5 http://127.0.0.1:8090/health 2>$null
if ($h1) { Write-Output $h1 } else { Write-Output "NO_RESPONSE" }

# If interactive-only task failed (no desktop session), fall back to schtasks create/run with breakaway via cmd start /b under a persistent method:
# Use scheduled task that runs whether user is logged on - but don't rewrite policy silently if /Run already worked.
if (-not $h1) {
  Write-Output "===FALLBACK_CREATE_BREAKAWAY==="
  # Launch via schtasks one-shot that is NOT tied to current SSH job:
  # /RU User requires password for non-interactive; try SYSTEM with access to user files may fail for AppData Python.
  # Prefer: cmd start with CREATE_BREAKAWAY via PowerShell Win32 CreateProcess.
  $cmd = @"
`$sig = @'
[DllImport("kernel32.dll", SetLastError=true)]
public static extern bool CreateProcess(
  string lpApplicationName, string lpCommandLine,
  IntPtr lpProcessAttributes, IntPtr lpThreadAttributes,
  bool bInheritHandles, uint dwCreationFlags,
  IntPtr lpEnvironment, string lpCurrentDirectory,
  ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr hObject);
[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
public struct STARTUPINFO { public int cb; public string lpReserved; public string lpDesktop; public string lpTitle; public int dwX; public int dwY; public int dwXSize; public int dwYSize; public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute; public int dwFlags; public short wShowWindow; public short cbReserved2; public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError; }
[StructLayout(LayoutKind.Sequential)] public struct PROCESS_INFORMATION { public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; }
'@
Add-Type -MemberDefinition `$sig -Name Native -Namespace Win32 -ErrorAction SilentlyContinue
`$CREATE_NEW_PROCESS_GROUP = 0x00000200
`$CREATE_BREAKAWAY_FROM_JOB = 0x01000000
`$DETACHED_PROCESS = 0x00000008
`$flags = `$CREATE_BREAKAWAY_FROM_JOB -bor `$CREATE_NEW_PROCESS_GROUP -bor `$DETACHED_PROCESS
`$si = New-Object Win32.Native+STARTUPINFO
`$si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf(`$si)
`$pi = New-Object Win32.Native+PROCESS_INFORMATION
`$line = 'cmd.exe /c "C:\Users\user\run_orch_2lg.cmd"'
`$ok = [Win32.Native]::CreateProcess(`$null, `$line, [IntPtr]::Zero, [IntPtr]::Zero, `$false, `$flags, [IntPtr]::Zero, 'C:\CornerstoneMock\CornerstoneAgent', [ref]`$si, [ref]`$pi)
Write-Output ("CreateProcess ok=" + `$ok + " pid=" + `$pi.dwProcessId + " err=" + [Runtime.InteropServices.Marshal]::GetLastWin32Error())
if (`$pi.hThread -ne [IntPtr]::Zero) { [Win32.Native]::CloseHandle(`$pi.hThread) | Out-Null }
if (`$pi.hProcess -ne [IntPtr]::Zero) { [Win32.Native]::CloseHandle(`$pi.hProcess) | Out-Null }
"@
  $tmp = Join-Path $env:TEMP "start_orch_breakaway.ps1"
  Set-Content -Path $tmp -Value $cmd -Encoding UTF8
  powershell -NoProfile -ExecutionPolicy Bypass -File $tmp
  Start-Sleep -Seconds 6
}

Write-Output "===HEALTH2==="
$h2 = & curl.exe -s -m 5 http://127.0.0.1:8090/health 2>$null
if ($h2) { Write-Output $h2 } else { Write-Output "NO_RESPONSE" }

Write-Output "===LISTEN==="
netstat -ano | findstr ":8090" | findstr LISTENING

Write-Output "===PROC==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*run*" } |
  ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }

Write-Output "===INSTRUMENTS_ONLINE==="
& curl.exe -s -m 90 "http://127.0.0.1:8090/v1/instruments?online_only=true"
Write-Output ""
Write-Output "===LOG_TAIL==="
Get-Content (Join-Path $root "logs\orch.out.log") -Tail 20 -ErrorAction SilentlyContinue
