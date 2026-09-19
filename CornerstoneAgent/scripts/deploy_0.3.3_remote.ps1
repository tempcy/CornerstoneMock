$ErrorActionPreference = "Stop"
$root = "C:\CornerstoneMock\CornerstoneAgent"
$zip = "C:\CornerstoneMock\csagent-0.3.3.zip"
$pyCandidates = @(
  "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe",
  "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
)
$py = $pyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "python not found" }

Write-Output "===EXPAND==="
if (-not (Test-Path $zip)) { throw "missing $zip" }
Expand-Archive -Path $zip -DestinationPath $root -Force
Write-Output "expanded to $root"
Get-Content (Join-Path $root "src\cornerstone_agent\__init__.py")

Write-Output "===PIP_E==="
# 现场常无外网/代理不通；任务一般直接跑源码树，pip 失败不阻断
Push-Location $root
try {
  & $py -m pip install -e . --disable-pip-version-check
  if ($LASTEXITCODE -ne 0) {
    Write-Output "pip install -e failed (exit=$LASTEXITCODE); continue with source tree"
  }
} catch {
  Write-Output "pip install -e skipped: $($_.Exception.Message)"
} finally {
  Pop-Location
}

Write-Output "===STOP_OLD==="
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    Write-Output "kill listen pid=$($_.OwningProcess)"
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*run*" } |
  ForEach-Object {
    Write-Output "kill python pid=$($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 2

Write-Output "===RUN_TASK==="
$r = schtasks /Run /TN "\CornerstoneAgent-Orchestrator" 2>&1
Write-Output $r
Start-Sleep -Seconds 8

Write-Output "===HEALTH==="
$h = & curl.exe -s -m 8 http://127.0.0.1:8090/health 2>$null
if ($h) { Write-Output $h } else { Write-Output "NO_RESPONSE_AFTER_TASK" }

if (-not $h) {
  Write-Output "===FALLBACK_START==="
  $logDir = Join-Path $root "logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $outLog = Join-Path $logDir "orch.out.log"
  $errLog = Join-Path $logDir "orch.err.log"
  $arg = "-u -m cornerstone_agent run --host 0.0.0.0 --port 8090"
  Start-Process -FilePath $py -ArgumentList $arg -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
  Start-Sleep -Seconds 6
  $h = & curl.exe -s -m 8 http://127.0.0.1:8090/health 2>$null
  if ($h) { Write-Output $h } else { Write-Output "NO_RESPONSE_AFTER_FALLBACK" }
  Write-Output "===ERR_TAIL==="
  Get-Content $errLog -Tail 30 -ErrorAction SilentlyContinue
}

Write-Output "===UI_CHECK==="
$ui = Join-Path $root "src\cornerstone_agent\ui_static\index.html"
Select-String -Path $ui -Pattern "<th>版本</th>|默认 Bridge" | ForEach-Object { $_.Line.Trim() }

Write-Output "===LISTEN==="
netstat -ano | findstr ":8090" | findstr LISTENING
Write-Output "===DONE==="
