from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "CornerstoneWeb/src/cornerstone_mock/mock_server.py").read_text(encoding="utf-8").splitlines(
    keepends=True
)

HEADER = '''from __future__ import annotations

import asyncio
import html
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .hub import GatewayHub
from .hub_types import PendingAddSamples
from .protocol import _async_close_stream_writer

'''

helpers = "".join(lines[3631:3730])  # _http_send through _q_bool
queue_fn = "".join(lines[1056:1065]).replace("PendingAddSamples", "PendingAddSamples")
body = "".join(lines[3732:4266])
body = body.replace("async def _handle_http(", "async def handle_bridge_http(", 1)
body = re.sub(
    r'        if method == "GET" and path\.startswith\("/static/"\):.*?return\n\n',
    "",
    body,
    count=1,
    flags=re.DOTALL,
)
body = re.sub(
    r'        if method == "GET" and path in \("/", "/index\.html"\):.*?return\n\n',
    "",
    body,
    count=1,
    flags=re.DOTALL,
)
body = re.sub(
    r'        if method == "GET" and path == "/legacy":.*?return\n\n',
    "",
    body,
    count=1,
    flags=re.DOTALL,
)
body = re.sub(
    r'        if method == "POST" and path == "/send":.*?return\n\n',
    "",
    body,
    count=1,
    flags=re.DOTALL,
)

out = ROOT / "CornerstoneBridge/src/cornerstone_bridge/http_api.py"
out.write_text(HEADER + helpers + "\n" + queue_fn + "\n" + body, encoding="utf-8")
print("wrote", out)
