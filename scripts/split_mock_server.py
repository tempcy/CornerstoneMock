"""One-off: split mock_server.py into cornerstone_bridge modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "CornerstoneWeb" / "src" / "cornerstone_mock" / "mock_server.py"
OUT = ROOT / "CornerstoneBridge" / "src" / "cornerstone_bridge"

COMMON_IMPORTS = '''from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import json
import math
import re
import secrets
import struct
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

from cornerstone_cli.communications.tcp_engine import HEARTBEAT_XML

'''

PROTOCOL_HEADER = COMMON_IMPORTS

PARSERS_HEADER = COMMON_IMPORTS + "from . import protocol as proto\n\n"

HUB_TYPES_HEADER = COMMON_IMPORTS

HUB_HELPERS_HEADER = (
    COMMON_IMPORTS
    + "from .parsers import _parse_remote_control_state_xml, _xml_local_tag\n"
    + "from .hub_types import PendingAddSamples\n\n"
)

HUB_HEADER = (
    COMMON_IMPORTS
    + "from .hub_types import PendingAddSamples, _FutureWaiter\n"
    + "from .protocol import *\n"
    + "from .parsers import *\n\n"
)

GATEWAY_HEADER = (
    COMMON_IMPORTS
    + "from .hub import GatewayHub, PendingAddSamples\n"
    + "from .protocol import (\n"
    + "    _add_samples_name_description,\n"
    + "    _async_close_stream_writer,\n"
    + "    _frame,\n"
    + "    _parse_cookie_from_payload,\n"
    + "    _peer_host_from_peername,\n"
    + "    _peer_host_matches_privileged,\n"
    + "    _root_tag,\n"
    + "    _synthetic_add_samples_held,\n"
    + "    _synthetic_logon_success,\n"
    + ")\n\n"
)

HTTP_HEADER = (
    COMMON_IMPORTS
    + "from .hub import GatewayHub\n"
    + "from .hub_types import PendingAddSamples\n"
    + "from .protocol import _async_close_stream_writer\n"
    + "from .gateway import _async_drain_remaining_tasks\n\n"
)


def slice_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "__init__.py").write_text(
        '"""Cornerstone TCP 网关、XML 解析与对内 REST API。"""\n', encoding="utf-8"
    )
    (OUT / "protocol.py").write_text(
        PROTOCOL_HEADER + slice_lines(lines, 25, 137), encoding="utf-8"
    )
    parsers_body = slice_lines(lines, 140, 1054) + slice_lines(lines, 1068, 2098)
    (OUT / "parsers.py").write_text(PARSERS_HEADER + parsers_body, encoding="utf-8")
    (OUT / "hub_types.py").write_text(
        HUB_TYPES_HEADER + slice_lines(lines, 2103, 2117), encoding="utf-8"
    )
    (OUT / "hub_helpers.py").write_text(
        HUB_HELPERS_HEADER + slice_lines(lines, 2120, 2249), encoding="utf-8"
    )
    hub_body = slice_lines(lines, 2251, 3499)
    (OUT / "hub.py").write_text(
        HUB_HEADER
        + "from .hub_helpers import *\n\n"
        + hub_body,
        encoding="utf-8",
    )
    (OUT / "gateway.py").write_text(
        GATEWAY_HEADER + slice_lines(lines, 3502, 3590), encoding="utf-8"
    )
    http_helpers = (
        slice_lines(lines, 3632, 3651)
        + slice_lines(lines, 3654, 3731)
    )
    http_body = slice_lines(lines, 3739, 4267)
    # Drop static/SPA routes from bridge HTTP (web serves them).
    skip_prefixes = (
        '        if method == "GET" and path.startswith("/static/"):',
        '        if method == "GET" and path in ("/", "/index.html"):',
        '        if method == "GET" and path == "/legacy":',
    )
    http_lines: list[str] = []
    skip = 0
    for line in http_body.splitlines(keepends=True):
        if skip > 0:
            skip -= 1
            continue
        if any(line.startswith(p) for p in skip_prefixes):
            skip = 12
            continue
        http_lines.append(line)
    (OUT / "http_api.py").write_text(
        HTTP_HEADER
        + http_helpers
        + "\n\n"
        + "def _queue_item_to_api_dict(p: PendingAddSamples) -> Dict[str, Any]:\n"
        + slice_lines(lines, 1057, 1065)
        + "\n\nasync def handle_bridge_http(\n"
        + "    reader: asyncio.StreamReader,\n"
        + "    writer: asyncio.StreamWriter,\n"
        + "    *,\n"
        + "    hub: GatewayHub,\n"
        + ") -> None:\n"
        + "".join(http_lines).replace("async def _handle_http(", "    # was _handle_http\n", 1),
        encoding="utf-8",
    )
    print("Wrote modules to", OUT)


if __name__ == "__main__":
    main()
