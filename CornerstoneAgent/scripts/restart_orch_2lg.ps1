$ErrorActionPreference = "Stop"
$root = "C:\CornerstoneMock\CornerstoneAgent"
$py = "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
Set-Location $root

Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
  }
Start-Sleep -Seconds 2

Start-Process -FilePath $py `
  -ArgumentList @("-u", "-m", "cornerstone_agent", "run", "--host", "0.0.0.0", "--port", "8090") `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $root "orch.out.log") `
  -RedirectStandardError (Join-Path $root "orch.err.log")

Start-Sleep -Seconds 8
Write-Output "===HEALTH==="
& curl.exe -s http://127.0.0.1:8090/health
Write-Output ""
Write-Output "===INSTRUMENTS==="
& curl.exe -s "http://127.0.0.1:8090/v1/instruments?online_only=false"
Write-Output ""
Write-Output "===ERRLOG==="
if (Test-Path (Join-Path $root "orch.err.log")) {
  Get-Content (Join-Path $root "orch.err.log") -Tail 40
}
