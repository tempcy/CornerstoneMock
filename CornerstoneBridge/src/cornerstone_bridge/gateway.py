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

from .bridge_logging import get_logger, log_gateway_xml
from .hub import GatewayHub, PendingAddSamples
from .hub_helpers import _peer_host_from_peername, _peer_host_matches_privileged
from .parsers import _xml_local_tag
from .protocol import (
    _add_samples_name_description,
    _async_close_stream_writer,
    _frame,
    _parse_cookie_from_payload,
    _root_tag,
    decode_frame_payload_bytes,
    frame_xml_defect,
    validate_frame_length,
    _synthetic_add_samples_held,
    _synthetic_logoff_success,
    _synthetic_logon_success,
)

_log = get_logger("client")


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    hub: GatewayHub,
    async_message_interval: float,
) -> None:
    if not hub.is_tcp_gateway_enabled():
        _log.info("client rejected (TCP gateway disabled): %s", writer.get_extra_info("peername"))
        await _async_close_stream_writer(writer)
        return

    peer = writer.get_extra_info("peername")
    peer_s = str(peer)
    _log.info("client connected: %s", peer_s)
    await hub.register_tcp_client(writer)

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
            length_err = validate_frame_length(length, enc)
            if length_err:
                _log.warning(
                    "client %s invalid frame length=%d (%s); closing",
                    peer_s,
                    length,
                    length_err,
                )
                break
            if length == 0:
                continue
            payload_bytes = await reader.readexactly(length)
            text, decode_err = decode_frame_payload_bytes(payload_bytes, enc)
            if decode_err:
                _log.warning(
                    "client %s frame decode error len=%d: %s; closing",
                    peer_s,
                    length,
                    decode_err,
                )
                break
            xml_err = frame_xml_defect(text or "")
            if xml_err:
                _log.warning(
                    "client %s bad frame len=%d xml=%s; closing",
                    peer_s,
                    length,
                    xml_err,
                )
                break

            tag = _xml_local_tag(_root_tag(text))
            cookie = _parse_cookie_from_payload(text)
            log_gateway_xml(_log, "client IN", text, cookie=cookie)
            hub.on_client_rx(writer)
            if tag == "Logon":
                hub.on_client_logon_request(writer, text)

            if tag == "Logon":
                if hub.should_synthesize_client_logon():
                    resp = _synthetic_logon_success(cookie)
                    _log.info("synthetic Logon for %s (gateway session)", peer_s)
                    hub.on_client_tx(writer)
                    hub.on_client_logon_response(writer, resp)
                    writer.write(_frame(resp, enc))
                    await writer.drain()
                    continue
                await hub.forward_client_frame(text, writer)
                continue

            if tag == "Logoff":
                if hub.should_synthesize_client_logon():
                    resp = _synthetic_logoff_success(cookie)
                    _log.info("synthetic Logoff for %s (gateway session)", peer_s)
                    hub.on_client_logoff(writer)
                    hub.on_client_tx(writer)
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
                    _log.info(
                        "AddSamples direct upstream (privileged host) peer=%s host=%r",
                        peer_s,
                        peer_host,
                    )
                    await hub.forward_client_frame(text, writer)
                    continue
                s_name, s_desc = _add_samples_name_description(text)
                hub.enqueue_add_samples(
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
                _log.info("AddSamples held -> queue size=%d", len(hub._pending_add_samples))
                hub.on_client_tx(writer)
                writer.write(_frame(resp, enc))
                await writer.drain()
                continue

            await hub.forward_client_frame(text, writer)
    except asyncio.IncompleteReadError:
        pass
    finally:
        await hub.unregister_tcp_client(writer)
        if async_task is not None:
            async_task.cancel()
            with contextlib.suppress(Exception):
                await async_task
        await _async_close_stream_writer(writer)
        _log.info("client disconnected: %s", peer_s)
