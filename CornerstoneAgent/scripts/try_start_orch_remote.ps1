$ErrorActionPreference = "Continue"
Write-Output "===CMD==="
Get-Content "C:\Users\user\run_orch_2lg.cmd"
Write-Output "===TRY==="
$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"
$src = "C:\CornerstoneMock\CornerstoneAgent\src"
$cfg = "C:\CornerstoneMock\CornerstoneAgent\cornerstone-agent.config.json"
$env:PYTHONPATH = $src
$env:CORNERSTONE_AGENT_CONFIG = $cfg
& $py -c "import traceback; import sys; sys.path.insert(0, r'C:\CornerstoneMock\CornerstoneAgent\src');
from cornerstone_agent.config import load_config
from cornerstone_agent.server import create_server
try:
    cfg = load_config()
    print('loaded', cfg.config_path, 'jobs', [j.id for j in cfg.timeseries.jobs], 'n', len(cfg.local_instruments()))
    srv, st = create_server(cfg)
    print('server_ok', st.cfg.timeseries.enabled)
    srv.server_close()
except Exception:
    traceback.print_exc()
"
