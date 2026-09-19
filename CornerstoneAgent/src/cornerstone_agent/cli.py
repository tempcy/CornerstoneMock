from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import load_config
from .dispatcher import ToolDispatcher
from .registry import AgentRegistry, DEFAULT_CAPABILITIES
from .server import serve_forever
from .timeseries import TimeseriesStore


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
    from .bridge_client import BridgeClient, extract_bridge_version

    bridge = BridgeClient(cfg.bridge.base_url, timeout_s=min(10.0, cfg.bridge.timeout_s))
    reachable, status = bridge.probe_status()
    versions = {"agent": __import__("cornerstone_agent").__version__}
    bv = extract_bridge_version(status)
    if bv:
        versions["bridge"] = bv
    rec = registry.register(
        agent_id=cfg.identity.agent_id,
        org_id=cfg.identity.org_id,
        lab_id=cfg.identity.lab_id,
        instrument_id=cfg.identity.instrument_id,
        bridge_url=cfg.bridge.base_url,
        capabilities=list(DEFAULT_CAPABILITIES),
        bridge_reachable=reachable,
        versions=versions,
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
    if getattr(args, "hours", None) is not None:
        payload["hours"] = args.hours
    if getattr(args, "metric", None):
        payload["metric"] = args.metric
    if getattr(args, "job", None):
        payload["job_id"] = args.job
    if getattr(args, "sample_id", None) is not None:
        payload["sample_id"] = args.sample_id

    from pathlib import Path

    snap = Path(cfg.orchestrator.snapshot_dir)
    if not snap.is_absolute():
        snap = cfg.resolve_data_path(cfg.orchestrator.snapshot_dir, "acquisition_snapshots")

    ts = None
    ts_cfg = None
    if cfg.timeseries.enabled:
        ts = TimeseriesStore(cfg.resolve_data_path(cfg.timeseries.db_path, "agent_timeseries.sqlite3"))
        ts_cfg = cfg.timeseries

    disp = ToolDispatcher(
        registry,
        redact_sample_names=cfg.privacy.redact_sample_names,
        snapshot_dir=str(snap),
        timeseries_store=ts,
        timeseries_cfg=ts_cfg,
    )
    out = disp.dispatch_tool(args.name, payload)
    _print_json(out)
    return 0 if out.get("ok") else 1


def _open_timeseries(cfg) -> TimeseriesStore:
    return TimeseriesStore(cfg.resolve_data_path(cfg.timeseries.db_path, "agent_timeseries.sqlite3"))


def cmd_collect_once(args: argparse.Namespace) -> int:
    from .bridge_client import BridgeClient
    from .collector import collect_once
    from .config import InstrumentEndpoint

    cfg = load_config(args.config)
    store = _open_timeseries(cfg)
    targets = list(cfg.local_instruments())
    if args.instrument_id:
        iid = str(args.instrument_id).strip()
        matched = [ep for ep in targets if ep.instrument_id == iid]
        if matched:
            targets = matched
        else:
            targets = [
                InstrumentEndpoint(
                    instrument_id=iid,
                    bridge_url=cfg.bridge.base_url,
                    agent_id=cfg.identity.agent_id,
                    lab_id=cfg.identity.lab_id,
                    org_id=cfg.identity.org_id,
                )
            ]
    elif not args.all and targets:
        default_id = cfg.identity.instrument_id
        filtered = [ep for ep in targets if ep.instrument_id == default_id]
        targets = filtered or targets[:1]

    timeout = min(float(cfg.timeseries.timeout_s), cfg.bridge.timeout_s)
    job_id = str(getattr(args, "job", None) or "").strip()
    jobs = []
    if job_id:
        job = cfg.timeseries.job_by_id(job_id)
        if job is None:
            print(f"error: unknown job '{job_id}'", file=sys.stderr)
            return 2
        jobs = [job]
    else:
        jobs = list(cfg.timeseries.enabled_jobs())
    if not jobs:
        print("error: no timeseries jobs enabled", file=sys.stderr)
        return 2
    results = []
    rc = 0
    for ep in targets:
        for job in jobs:
            if not job.matches_instrument(ep.instrument_id):
                continue
            out = collect_once(
                bridge=BridgeClient(ep.bridge_url, timeout_s=timeout),
                store=store,
                instrument_id=ep.instrument_id,
                lab_id=ep.lab_id,
                agent_id=ep.agent_id,
                endpoints=job.endpoints,
                job_id=job.id,
            )
            results.append(out)
            if not out.get("ok"):
                rc = 1
    store.close()
    _print_json({"ok": rc == 0, "results": results})
    return rc


def cmd_timeseries_query(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    store = _open_timeseries(cfg)
    iid = str(args.instrument_id or cfg.identity.instrument_id).strip()
    hours = float(args.hours)
    job_id = str(getattr(args, "job", None) or "").strip() or None
    payload: dict[str, Any] = {
        "ok": True,
        "instrument_id": iid,
        "hours": hours,
        "job_id": job_id or "",
        "stats": store.sample_stats(iid, hours=hours, job_id=job_id),
        "metrics": store.list_metrics(iid, hours=hours, job_id=job_id),
        "samples": store.list_samples(iid, hours=hours, limit=int(args.limit), job_id=job_id),
        "latest": store.latest_values(iid, job_id=job_id),
    }
    if args.metric:
        payload["metric"] = args.metric
        payload["series"] = store.query_series(iid, args.metric, hours=hours, job_id=job_id)
    store.close()
    _print_json(payload)
    return 0


def cmd_timeseries_export(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    store = _open_timeseries(cfg)
    iid = str(args.instrument_id or cfg.identity.instrument_id).strip()
    job_id = str(getattr(args, "job", None) or "").strip() or None
    csv_text = store.export_csv(
        iid, hours=float(args.hours), metric=args.metric or None, job_id=job_id
    )
    store.close()
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(csv_text, encoding="utf-8")
        _print_json({"ok": True, "path": args.out, "bytes": len(csv_text.encode("utf-8"))})
    else:
        sys.stdout.write(csv_text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cornerstone-agent", description="Cornerstone Agent (C1 + A1)")
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
            "get_timeseries_latest",
            "list_timeseries_metrics",
            "query_timeseries",
            "get_timeseries_sample",
        ],
    )
    tool.add_argument("--lab-id", default=None)
    tool.add_argument("--instrument-id", default=None)
    tool.add_argument("--set-key", default=None)
    tool.add_argument("--number", type=int, default=None)
    tool.add_argument("--hours", type=float, default=None, help="timeseries window hours")
    tool.add_argument("--metric", default=None, help="timeseries metric name")
    tool.add_argument("--job", default=None, help="timeseries job_id (widgets / ambients)")
    tool.add_argument("--sample-id", type=int, default=None, help="get_timeseries_sample")
    tool.add_argument(
        "--profile",
        default=None,
        choices=["status_light", "analysis_recent", "troubleshoot", "custom"],
        help="collect_instrument profile",
    )
    tool.add_argument("--args-json", default=None, help='Extra JSON object, e.g. \'{"number":5}\'')
    tool.set_defaults(func=cmd_tool)

    col = sub.add_parser("collect", help="A1 采集（写入 SQLite 时序）")
    col_sub = col.add_subparsers(dest="collect_cmd", required=True)
    once = col_sub.add_parser("once", help="立即采集一次并落盘")
    once.add_argument("--instrument-id", default=None)
    once.add_argument("--all", action="store_true", help="配置中全部仪器各采一次")
    once.add_argument("--job", default=None, help="任务 id（widgets / ambients）；省略则跑全部匹配任务")
    once.set_defaults(func=cmd_collect_once)

    ts = sub.add_parser("timeseries", help="查询 / 导出 SQLite 时序")
    ts_sub = ts.add_subparsers(dest="ts_cmd", required=True)
    tsq = ts_sub.add_parser("query", help="查询 24h（默认）样本与曲线")
    tsq.add_argument("--instrument-id", default=None)
    tsq.add_argument("--hours", type=float, default=24.0)
    tsq.add_argument("--metric", default=None)
    tsq.add_argument("--job", default=None, help="过滤任务 id")
    tsq.add_argument("--limit", type=int, default=200)
    tsq.set_defaults(func=cmd_timeseries_query)
    tse = ts_sub.add_parser("export", help="导出 CSV")
    tse.add_argument("--instrument-id", default=None)
    tse.add_argument("--hours", type=float, default=24.0)
    tse.add_argument("--metric", default=None)
    tse.add_argument("--job", default=None, help="过滤任务 id")
    tse.add_argument("--out", default=None, help="输出文件；省略则打印到 stdout")
    tse.set_defaults(func=cmd_timeseries_export)

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
