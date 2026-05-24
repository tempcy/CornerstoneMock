from __future__ import annotations

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

@dataclass
class PendingAddSamples:
    entry_id: str
    received_at: float
    source_peer: str
    payload_xml: str
    sample_name: str = ""
    sample_description: str = ""


@dataclass
class _FutureWaiter:
    """上游应答按 Cookie 路由时，网页触发的请求用 Future 收文，不写回 TCP。"""

    fut: asyncio.Future[str]


@dataclass
class TcpClientSession:
    """TCP 远程客户端会话（供 /api/monitor 与连接开关）。"""

    writer: asyncio.StreamWriter
    peer: str
    peer_host: str
    connected_at: float
    rx_frames: int = 0
    tx_frames: int = 0
    logon_user: str = ""
    logon_authenticated: bool = False
    privileged: bool = False
