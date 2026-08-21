from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import load_config
from .dispatcher import ToolDispatcher
from .registry import AgentRegistry, DEFAULT_CAPABILITIES
from .server import serve_forever


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.host:
        cfg.orchestrator.listen_host = args.host
    if args.port:
        cfg.orchestrator.listen_port = int(args.port)
    if args.no_embed:
        cfg.orchestrator.embed_local_agent = False
    serve_forever(cfg)
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    registry = AgentRegistry(cfg.orchestrator.registry_path, cfg.orchestrator.online_ttl_s)
    from .bridge_client import BridgeClient

    bridge = BridgeClient(cfg.bridge.base_url, timeout_s=min(10.0, cfg.bridge.timeout_s))
    reachable = bridge.ping()
    rec = registry.register(
        agent_id=cfg.identity.agent_id,
        org_id=cfg.identity.org_id,
        lab_id=cfg.identity.lab_id,
        instrument_id=cfg.identity.instrument_id,
        bridge_url=cfg.bridge.base_url,
        capabilities=list(DEFAULT_CAPABILITIES),
        bridge_reachable=reachable,
        versions={"agent": __import__("cornerstone_agent").__version__},
    )
    _print_json({"ok": True, "agent": rec.to_public(online=True), "bridge_reachable": reachable})
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    registry = AgentRegistry(cfg.orchestrator.registry_path, cfg.orchestrator.online_ttl_s)
    disp = ToolDispatcher(registry, redact_sample_names=cfg.privacy.redact_sample_names)
    _print_json(
        disp.list_instruments(
            {"lab_id": args.lab_id, "online_only": not args.all}
        )
    )
    return 0


def cmd_tool(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    registry = AgentRegistry(cfg.orchestrator.registry_path, cfg.orchestrator.online_ttl_s)
    # ensure local agent present for offline CLI use
    if registry.lookup(cfg.identity.lab_id, cfg.identity.instrument_id) is None:
        from .bridge_client import BridgeClient

        bridge = BridgeClient(cfg.bridge.base_url, timeout_s=min(10.0, cfg.bridge.timeout_s))
        registry.register(
            agent_id=cfg.identity.agent_id,
            org_id=cfg.identity.org_id,
            lab_id=cfg.identity.lab_id,
            instrument_id=cfg.identity.instrument_id,
            bridge_url=cfg.bridge.base_url,
            capabilities=list(DEFAULT_CAPABILITIES),
            bridge_reachable=bridge.ping(),
        )

    payload: dict[str, Any] = {}
    if args.args_json:
        payload = json.loads(args.args_json)
    if args.lab_id:
        payload["lab_id"] = args.lab_id
    if args.instrument_id:
        payload["instrument_id"] = args.instrument_id
    payload.setdefault("lab_id", cfg.identity.lab_id)
    payload.setdefault("instrument_id", cfg.identity.instrument_id)
    if args.set_key:
        payload["set_key"] = args.set_key
    if args.number is not None:
        payload["number"] = args.number
    if getattr(args, "profile", None):
        payload["profile"] = args.profile

    from pathlib import Path

    snap = Path(cfg.orchestrator.snapshot_dir)
    if not snap.is_absolute():
        base = Path(cfg.config_path).resolve().parent if cfg.config_path else Path.cwd()
        snap = base / snap

    disp = ToolDispatcher(
        registry,
        redact_sample_names=cfg.privacy.redact_sample_names,
        snapshot_dir=str(snap),
    )
    out = disp.dispatch_tool(args.name, payload)
    _print_json(out)
    return 0 if out.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cornerstone-agent", description="Cornerstone Agent C1 POC")
    p.add_argument(
        "--config",
        default=None,
        help="config JSON path (or CORNERSTONE_AGENT_CONFIG / CWD cornerstone-agent.config.json)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Start orchestrator HTTP + optional embedded local agent")
    run.add_argument("--host", default=None)
    run.add_argument("--port", type=int, default=None)
    run.add_argument("--no-embed", action="store_true", help="Do not auto-register local agent")
    run.set_defaults(func=cmd_run)

    reg = sub.add_parser("register", help="One-shot register local identity into registry file")
    reg.set_defaults(func=cmd_register)

    ls = sub.add_parser("list-instruments", help="List registry instruments")
    ls.add_argument("--lab-id", default=None)
    ls.add_argument("--all", action="store_true", help="Include offline agents")
    ls.set_defaults(func=cmd_list)

    tool = sub.add_parser("tool", help="Invoke a P0/P1 tool against registry + Bridge")
    tool.add_argument(
        "name",
        choices=[
            "list_instruments",
            "get_instrument_status",
            "get_analysis_sets",
            "get_set_reps",
            "collect_instrument",
        ],
    )
    tool.add_argument("--lab-id", default=None)
    tool.add_argument("--instrument-id", default=None)
    tool.add_argument("--set-key", default=None)
    tool.add_argument("--number", type=int, default=None)
    tool.add_argument(
        "--profile",
        default=None,
        choices=["status_light", "analysis_recent", "troubleshoot", "custom"],
        help="collect_instrument profile",
    )
    tool.add_argument("--args-json", default=None, help='Extra JSON object, e.g. \'{"number":5}\'')
    tool.set_defaults(func=cmd_tool)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
