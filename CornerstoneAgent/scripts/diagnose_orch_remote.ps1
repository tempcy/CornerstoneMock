$ErrorActionPreference = "Continue"
Write-Output "===TASK==="
schtasks /Query /TN "\CornerstoneAgent-Orchestrator" /FO LIST /V
Write-Output "===PORT==="
netstat -ano | findstr ":8090"
Write-Output "===PROC==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  ForEach-Object { Write-Output ("{0} {1}" -f $_.ProcessId, $_.CommandLine) }
Write-Output "===IMPORT==="
$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"
& $py -c "import sys; sys.path.insert(0, r'C:\CornerstoneMock\CornerstoneAgent\src'); import cornerstone_agent; print('ver', cornerstone_agent.__version__)"
Write-Output "===CONFIG_TS==="
& $py -c "import json; p=r'C:\CornerstoneMock\CornerstoneAgent\cornerstone-agent.config.json'; d=json.load(open(p,encoding='utf-8')); ts=d.get('timeseries') or {}; print('jobs', [j.get('id') for j in (ts.get('jobs') or [])]); print('n_inst', len(d.get('instruments') or []))"
