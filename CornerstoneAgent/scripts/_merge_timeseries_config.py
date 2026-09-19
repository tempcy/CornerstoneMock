import json
from pathlib import Path

p = Path(r"C:\CornerstoneMock\CornerstoneAgent\cornerstone-agent.config.json")
d = json.loads(p.read_text(encoding="utf-8"))
ts = d.get("timeseries") if isinstance(d.get("timeseries"), dict) else {}
d["timeseries"] = {
    "enabled": True if "enabled" not in ts else bool(ts.get("enabled")),
    "db_path": str(ts.get("db_path") or "agent_timeseries.sqlite3"),
    "timeout_s": float(ts.get("timeout_s") or 90),
    "jobs": [
        {
            "id": "widgets",
            "enabled": True,
            "label": "实时仪表 Widgets",
            "endpoints": ["status-widgets"],
            "interval_s": 10,
            "retention_days": 3,
            "scope": "all",
            "instrument_ids": [],
        },
        {
            "id": "ambients",
            "enabled": True,
            "label": "环境 Ambients",
            "endpoints": ["ambients"],
            "interval_s": 300,
            "retention_days": 90,
            "scope": "all",
            "instrument_ids": [],
        },
    ],
}
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("ok jobs", [j["id"] for j in d["timeseries"]["jobs"]])
print("instruments", len(d.get("instruments") or []))
