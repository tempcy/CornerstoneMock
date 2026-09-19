from pathlib import Path

html = Path(r"C:\CornerstoneMock\CornerstoneAgent\src\cornerstone_agent\ui_static\index.html").read_text(encoding="utf-8")
js = Path(r"C:\CornerstoneMock\CornerstoneAgent\src\cornerstone_agent\ui_static\app.js").read_text(encoding="utf-8")
init = Path(r"C:\CornerstoneMock\CornerstoneAgent\src\cornerstone_agent\__init__.py").read_text(encoding="utf-8")
print("init", "0.3.3" in init)
print("version_col", "<th>版本</th>" in html)
print("default_bridge_card", "默认 Bridge" in html)
print("cfg-bridge", 'id="cfg-bridge"' in html)
print("bridgeVersionLabel", "bridgeVersionLabel" in js)
