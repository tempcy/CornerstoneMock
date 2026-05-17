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

from .hub import GatewayHub, PendingAddSamples
from .hub_helpers import _peer_host_from_peername, _peer_host_matches_privileged
from .protocol import (
    _add_samples_name_description,
    _async_close_stream_writer,
    _frame,
    _parse_cookie_from_payload,
    _root_tag,
    _synthetic_add_samples_held,
    _synthetic_logon_success,
)

async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    hub: GatewayHub,
    async_message_interval: float,
) -> None:
    peer = writer.get_extra_info("peername")
    peer_s = str(peer)
    print(f"[gateway] client connected: {peer_s}")

    async_task: Optional[asyncio.Task[None]] = None

    async def push_async_messages() -> None:
        n = 0
        while True:
            await asyncio.sleep(async_message_interval)
            n += 1
            msg = f"<CornerstoneMessage><Text>Hello {n}</Text></CornerstoneMessage>"
            writer.write(_frame(msg, hub.encoding))
            await writer.drain()

    if async_message_interval > 0:
        async_task = asyncio.create_task(push_async_messages(), name="gateway_async_messages")

    enc = hub.encoding
    try:
        while True:
            header = await reader.readexactly(4)
            (length,) = struct.unpack("<I", header)
            if length == 0:
                continue
            payload_bytes = await reader.readexactly(length)
            text = payload_bytes.decode(enc, errors="replace")
            print(f"[gateway] client IN: {text[:400]}{'...' if len(text) > 400 else ''}")

            tag = _root_tag(text)
            cookie = _parse_cookie_from_payload(text)

            if tag == "Logon":
                if hub._synthetic_logon_after_first and hub._logon_seen_upstream_success:
                    resp = _synthetic_logon_success(cookie)
                    print(f"[gateway] synthetic Logon for {peer_s}")
                    writer.write(_frame(resp, enc))
                    await writer.drain()
                    continue
                await hub.forward_client_frame(text, writer)
                continue

            if tag == "AddSamples":
                peer_host = _peer_host_from_peername(peer)
                direct_upstream = _peer_host_matches_privileged(
                    peer_host, hub._privileged_add_samples_host
                )
                if direct_upstream:
                    print(
                        f"[gateway] AddSamples direct upstream (privileged host) "
                        f"peer={peer_s} host={peer_host!r}"
                    )
                    await hub.forward_client_frame(text, writer)
                    continue
                s_name, s_desc = _add_samples_name_description(text)
                hub._pending_add_samples.append(
                    PendingAddSamples(
                        entry_id=secrets.token_hex(8),
                        received_at=time.time(),
                        source_peer=peer_s,
                        payload_xml=text,
                        sample_name=s_name,
                        sample_description=s_desc,
                    )
                )
                resp = _synthetic_add_samples_held(cookie)
                print(f"[gateway] AddSamples held -> queue size={len(hub._pending_add_samples)}")
                writer.write(_frame(resp, enc))
                await writer.drain()
                continue

            await hub.forward_client_frame(text, writer)
    except asyncio.IncompleteReadError:
        pass
    finally:
        if async_task is not None:
            async_task.cancel()
            with contextlib.suppress(Exception):
                await async_task
        await _async_close_stream_writer(writer)
        print(f"[gateway] client disconnected: {peer_s}")

