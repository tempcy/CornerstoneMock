from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .config import AgentConfig
from .dispatcher import ToolDispatcher
from .envelopes import make_uplink
from .registry import DEFAULT_CAPABILITIES, AgentRegistry
from .ui_api import build_config_view, build_overview, ping_instrument

UI_STATIC_DIR = Path(__file__).resolve().parent / "ui_static"


def _resolve_snapshot_dir(cfg: AgentConfig) -> str:
    raw = (cfg.orchestrator.snapshot_dir or "acquisition_snapshots").strip()
    p = Path(raw)
    if not p.is_absolute():
        base = Path(cfg.config_path).resolve().parent if cfg.config_path else Path.cwd()
        p = base / p
    return str(p)


class OrchestratorState:
    def __init__(self, cfg: AgentConfig, registry: AgentRegistry, dispatcher: ToolDispatcher):
        self.cfg = cfg
        self.registry = registry
        self.dispatcher = dispatcher
        self.started_at = time.time()


def _json_bytes(obj: Any, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def _safe_ui_file(rel: str) -> Path | None:
    """Resolve path under ui_static; reject traversal."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = UI_STATIC_DIR.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def make_handler(state: OrchestratorState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[orch] {self.address_string()} {fmt % args}")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be object")
            return data

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj: Any, status: int = 200) -> None:
            st, body, ct = _json_bytes(obj, status)
            self._send(st, body, ct)

        def _send_redirect(self, location: str, status: int = 302) -> None:
            body = b""
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.wfile.write(body)

        def _serve_ui_file(self, rel: str) -> bool:
            path = _safe_ui_file(rel)
            if path is None:
                return False
            data = path.read_bytes()
            ctype, _ = mimetypes.guess_type(str(path))
            if not ctype:
                ctype = "application/octet-stream"
            if ctype.startswith("text/") or ctype in (
                "application/javascript",
                "application/json",
            ):
                ctype = f"{ctype}; charset=utf-8"
            self._send(200, data, ctype)
            return True

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/ui"):
                self._send_redirect("/ui/")
                return

            if path == "/ui/" or path == "/ui/index.html":
                if self._serve_ui_file("index.html"):
                    return
                self._send_json({"ok": False, "error": "ui_missing"}, status=500)
                return

            if path.startswith("/ui/"):
                rel = unquote(path[len("/ui/") :])
                if self._serve_ui_file(rel):
                    return
                self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)
                return

            if path in ("/health", "/v1/health"):
                self._send_json(
                    {
                        "ok": True,
                        "service": "cornerstone-agent-orchestrator",
                        "version": __version__,
                    }
                )
                return

            if path == "/api/ui/overview":
                self._send_json(
                    build_overview(
                        state.cfg,
                        state.registry,
                        started_at=state.started_at,
                    )
                )
                return

            if path == "/api/ui/config":
                self._send_json(build_config_view(state.cfg))
                return

            if path == "/v1/instruments":
                lab = (qs.get("lab_id") or [None])[0]
                online_only = (qs.get("online_only") or ["true"])[0].lower() not in (
                    "0",
                    "false",
                    "no",
                )
                self._send_json(
                    state.dispatcher.list_instruments(
                        {"lab_id": lab, "online_only": online_only}
                    )
                )
                return

            self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": "invalid_json", "message": str(e)}, status=400)
                return

            if path == "/api/ui/instruments/ping":
                instrument_id = str(body.get("instrument_id") or "").strip()
                if not instrument_id:
                    self._send_json(
                        {"ok": False, "error": "instrument_id required"},
                        status=400,
                    )
                    return
                out = ping_instrument(state.cfg, state.registry, instrument_id)
                status = 200 if out.get("ok") else 404
                self._send_json(out, status=status)
                return

            if path == "/v1/agents/register":
                lab_id = str(body.get("lab_id") or "").strip()
                instrument_id = str(body.get("instrument_id") or "").strip()
                if not lab_id or not instrument_id:
                    self._send_json(
                        {"ok": False, "error": "lab_id and instrument_id required"},
                        status=400,
                    )
                    return
                rec = state.registry.register(
                    agent_id=str(body.get("agent_id") or "") or None,
                    org_id=str(body.get("org_id") or state.cfg.identity.org_id),
                    lab_id=lab_id,
                    instrument_id=instrument_id,
                    bridge_url=str(body.get("bridge_url") or ""),
                    capabilities=list(body.get("capabilities") or DEFAULT_CAPABILITIES),
                    bridge_reachable=bool(body.get("bridge_reachable", False)),
                    versions=dict(body.get("versions") or {}),
                )
                self._send_json(
                    {
                        "ok": True,
                        "agent": rec.to_public(online=True),
                        "uplink": make_uplink(
                            "register",
                            agent_id=rec.agent_id,
                            org_id=rec.org_id,
                            lab_id=rec.lab_id,
                            instrument_id=rec.instrument_id,
                            bridge_reachable=rec.bridge_reachable,
                            capabilities=rec.capabilities,
                            versions=rec.versions,
                        ),
                    }
                )
                return

            if path == "/v1/agents/heartbeat":
                agent_id = str(body.get("agent_id") or "").strip()
                rec = state.registry.heartbeat(
                    agent_id,
                    bridge_reachable=body.get("bridge_reachable"),
                    capabilities=body.get("capabilities"),
                    versions=body.get("versions"),
                )
                if rec is None:
                    self._send_json(
                        {"ok": False, "error": "unknown_agent", "agent_id": agent_id},
                        status=404,
                    )
                    return
                self._send_json(
                    {
                        "ok": True,
                        "agent": rec.to_public(online=True),
                        "uplink": make_uplink(
                            "heartbeat",
                            agent_id=rec.agent_id,
                            org_id=rec.org_id,
                            lab_id=rec.lab_id,
                            instrument_id=rec.instrument_id,
                            bridge_reachable=rec.bridge_reachable,
                            capabilities=rec.capabilities,
                            versions=rec.versions,
                        ),
                    }
                )
                return

            if path.startswith("/v1/tools/"):
                name = path[len("/v1/tools/") :].strip("/")
                if not name:
                    self._send_json({"ok": False, "error": "missing tool name"}, status=400)
                    return
                out = state.dispatcher.dispatch_tool(name, body)
                status = 200 if out.get("ok") else 400
                err = (out.get("error") or {}) if isinstance(out.get("error"), dict) else {}
                code = err.get("code")
                if code in ("instrument_not_found", "agent_offline"):
                    status = 404
                self._send_json(out, status=status)
                return

            if path == "/v1/jobs":
                lab_id = str(body.get("lab_id") or "").strip()
                instrument_id = str(body.get("instrument_id") or "").strip()
                job = body.get("job")
                if not isinstance(job, dict):
                    self._send_json({"ok": False, "error": "job object required"}, status=400)
                    return
                result = state.dispatcher.dispatch_job_raw(
                    job, lab_id=lab_id, instrument_id=instrument_id
                )
                self._send_json({"ok": result.get("status") == "ok", "result": result})
                return

            self._send_json({"ok": False, "error": "not_found", "path": path}, status=404)

    return Handler


def create_server(cfg: AgentConfig) -> tuple[ThreadingHTTPServer, OrchestratorState]:
    registry = AgentRegistry(
        cfg.orchestrator.registry_path,
        online_ttl_s=cfg.orchestrator.online_ttl_s,
    )
    dispatcher = ToolDispatcher(
        registry,
        redact_sample_names=cfg.privacy.redact_sample_names,
        snapshot_dir=_resolve_snapshot_dir(cfg),
    )
    state = OrchestratorState(cfg, registry, dispatcher)
    handler = make_handler(state)
    server = ThreadingHTTPServer(
        (cfg.orchestrator.listen_host, cfg.orchestrator.listen_port),
        handler,
    )
    return server, state


class LocalAgentHeartbeater:
    """嵌入式本地 Agent：启动时 register，周期 heartbeat + Bridge ping。"""

    def __init__(self, state: OrchestratorState):
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._tick(force_register=True)
        interval = max(5.0, float(self.state.cfg.orchestrator.heartbeat_interval_s))
        self._thread = threading.Thread(
            target=self._loop,
            args=(interval,),
            name="local-agent-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self._tick(force_register=False)

    def _tick(self, *, force_register: bool) -> None:
        try:
            self._tick_inner(force_register=force_register)
        except Exception as exc:  # noqa: BLE001 — 心跳失败不得拖垮编排进程
            print(f"[agent] heartbeat tick failed: {exc}")

    def _tick_inner(self, *, force_register: bool) -> None:
        from .bridge_client import BridgeClient

        cfg = self.state.cfg
        versions = {"agent": __version__}
        for ep in cfg.local_instruments():
            bridge = BridgeClient(ep.bridge_url, timeout_s=min(10.0, cfg.bridge.timeout_s))
            reachable = bridge.ping()
            if force_register or self.state.registry.get(ep.agent_id) is None:
                rec = self.state.registry.register(
                    agent_id=ep.agent_id,
                    org_id=ep.org_id,
                    lab_id=ep.lab_id,
                    instrument_id=ep.instrument_id,
                    bridge_url=ep.bridge_url,
                    capabilities=list(DEFAULT_CAPABILITIES),
                    bridge_reachable=reachable,
                    versions=versions,
                )
                print(
                    f"[agent] registered {rec.agent_id} "
                    f"{rec.lab_id}/{rec.instrument_id} bridge_ok={reachable}"
                )
            else:
                rec = self.state.registry.heartbeat(
                    ep.agent_id,
                    bridge_reachable=reachable,
                    capabilities=list(DEFAULT_CAPABILITIES),
                    versions=versions,
                )
                if rec:
                    print(
                        f"[agent] heartbeat {rec.agent_id} bridge_ok={reachable} "
                        f"at {rec.last_seen}"
                    )


def serve_forever(cfg: AgentConfig) -> None:
    server, state = create_server(cfg)
    heart: LocalAgentHeartbeater | None = None
    if cfg.orchestrator.embed_local_agent:
        heart = LocalAgentHeartbeater(state)
        heart.start()
    host, port = cfg.orchestrator.listen_host, cfg.orchestrator.listen_port
    print(f"[orch] listening http://{host}:{port}/  config={cfg.config_path}")
    print(
        "[orch] endpoints: /ui/ /api/ui/* /health /v1/instruments "
        "/v1/agents/register|heartbeat /v1/tools/*"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[orch] shutting down")
    finally:
        if heart:
            heart.stop()
        server.server_close()
