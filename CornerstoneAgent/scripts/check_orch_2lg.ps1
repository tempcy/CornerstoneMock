$root = "C:\CornerstoneMock\CornerstoneAgent"
Write-Output "===HEALTH==="
try {
  $h = & curl.exe -s -m 5 http://127.0.0.1:8090/health 2>$null
  if ($h) { Write-Output $h } else { Write-Output "NO_RESPONSE" }
} catch {
  Write-Output "NO_RESPONSE"
}
Write-Output "===LISTEN==="
netstat -ano | findstr ":8090"
Write-Output "===PROC==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*cornerstone_agent*" } |
  ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }
Write-Output "===ERR_TAIL==="
$err = Join-Path $root "orch.err.log"
if (Test-Path $err) { Get-Content $err -Tail 40 } else { Write-Output "(no orch.err.log)" }
Write-Output "===OUT_TAIL==="
$out = Join-Path $root "orch.out.log"
if (Test-Path $out) { Get-Content $out -Tail 40 } else { Write-Output "(no orch.out.log)" }
