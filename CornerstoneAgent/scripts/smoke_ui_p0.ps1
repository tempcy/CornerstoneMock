Write-Output "===UI==="
& curl.exe -s -D - -o NUL http://127.0.0.1:8090/ui/ 2>$null | Select-Object -First 8
Write-Output "===OVERVIEW==="
& curl.exe -s http://127.0.0.1:8090/api/ui/overview
Write-Output ""
Write-Output "===CONFIG==="
& curl.exe -s http://127.0.0.1:8090/api/ui/config
Write-Output ""
Write-Output "===PING_GO7==="
$body = '{"instrument_id":"GO7"}'
$body | Set-Content -Path "$env:TEMP\ping_go7.json" -Encoding ascii -NoNewline
& curl.exe -s -X POST http://127.0.0.1:8090/api/ui/instruments/ping -H "Content-Type: application/json" --data-binary "@$env:TEMP\ping_go7.json"
Write-Output ""
