from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape
from xml.etree import ElementTree as ET

from .communications.http_requestor import WebRequestor, build_post_data, build_user_lab_info_xml
from .communications.tcp_engine import AsyncTcpCommunicationEngine, TcpEncoding


def _encoding_from_arg(value: str) -> TcpEncoding:
    value = value.strip().lower()
    if value in ("utf16", "utf-16", "unicode", "utf-16le", "utf-16-le"):
        return TcpEncoding.utf16
    if value in ("utf8", "utf-8"):
        return TcpEncoding.utf8
    if value in ("ascii",):
        return TcpEncoding.ascii
    raise argparse.ArgumentTypeError(f"不支持的 encoding: {value}（可选: utf16/utf8/ascii）")


def _bool_from_arg(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("1", "true", "t", "yes", "y", "on"):
        return True
    if v in ("0", "false", "f", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError(f"不支持的布尔值: {value}（可选: true/false）")


def _logon_xml(user: str, password: str) -> str:
    """构造 <Logon> 请求体（对文本做 XML 转义）。"""
    return (
        f"<Logon><User>{_xml_escape(user)}</User>"
        f"<Password>{_xml_escape(password)}</Password></Logon>"
    )


def _logon_response_success(resp: Optional[str]) -> bool:
    """判断 Logon 应答是否表示认证成功（ErrorCode=0 且 ErrorMessage=Success）。"""
    if not resp or not (resp or "").strip().startswith("<"):
        return False
    try:
        root = ET.fromstring(resp.strip())
    except ET.ParseError:
        return False
    if root.tag != "Logon":
        return False
    ec = (root.attrib.get("ErrorCode") or "").strip()
    if ec != "0":
        return False
    em = (root.attrib.get("ErrorMessage") or "").strip()
    return em == "" or em.lower() == "success"


async def _tcp_ensure_logon(
    engine: AsyncTcpCommunicationEngine,
    username: str,
    password: str,
    *,
    timeout_s: float,
) -> bool:
    """发送 Logon，仅在应答成功时返回 True。"""
    xml = _logon_xml(username, password)
    try:
        resp = await engine.send_xml(xml, cookie="LOGON", timeout_s=timeout_s)
    except (RuntimeError, asyncio.TimeoutError, OSError) as e:
        print(f"认证失败: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"认证失败: {e}", file=sys.stderr)
        return False
    if _logon_response_success(resp):
        return True
    print(f"认证未通过（需要 <Logon ErrorCode=\"0\" ErrorMessage=\"Success\"/>）: {resp or '(无响应)'}", file=sys.stderr)
    return False


# Remote Sample Login / Remote Query：执行前须先成功 Logon（单次命令通过 --username/--password）
_TCP_CMDS_REQUIRE_LOGON: frozenset[str] = frozenset(
    {
        "last-remote-added-sets",
        "add-samples",
        "ambient",
        "ambients",
        "automation-status",
        "available-logs",
        "counter",
        "counters",
        "detectors",
        "double-value",
        "double-values",
        "exception-directory",
        "field",
        "fields",
        "filters",
        "gas-state",
        "log-data",
        "log-directory",
        "message-history",
        "method",
        "methods",
        "mondo-data",
        "mondo-directory",
        "next-to-analyze",
        "prerequisite",
        "prerequisites",
        "qc-status",
        "rep-detail",
        "rep-plot",
        "report",
        "reports",
        "sequence",
        "sequences",
        "set",
        "set-keys-ex2",
        "set-reps",
        "sets",
        "solenoid",
        "solenoids",
        "standard",
        "standards",
        "status",
        "string-value",
        "string-values",
        "switch",
        "switches",
        "system-parameters",
        "transport",
        "transports",
        "valve-states",
    }
)

# session 中首词与 tcp 子命令名一致（小写）
_SESSION_FIRST_TOKEN_REQUIRES_LOGON: frozenset[str] = _TCP_CMDS_REQUIRE_LOGON

# 直接粘贴的 XML 根元素需先登录
_XML_ROOT_TAGS_REQUIRE_LOGON: frozenset[str] = frozenset(
    {
        "AddSamples",
        "LastRemoteAddedSets",
        "Ambient",
        "Ambients",
        "AutomationStatus",
        "AvailableLogs",
        "Counter",
        "Counters",
        "Detectors",
        "DoubleValue",
        "DoubleValues",
        "ExceptionDirectory",
        "Field",
        "Fields",
        "Filters",
        "GasState",
        "LogData",
        "LogDirectory",
        "MessageHistory",
        "Method",
        "Methods",
        "MondoData",
        "MondoDirectory",
        "NextToAnalyze",
        "Prerequisite",
        "Prerequisites",
        "QCStatus",
        "RepDetail",
        "RepPlot",
        "Report",
        "Reports",
        "Sequence",
        "Sequences",
        "Set",
        "SetKeysEx2",
        "SetReps",
        "Sets",
        "Solenoid",
        "Solenoids",
        "Standard",
        "Standards",
        "Status",
        "StringValue",
        "StringValues",
        "Switch",
        "Switches",
        "SystemParameters",
        "Transport",
        "Transports",
        "ValveStates",
    }
)


def _session_input_requires_logon(line: str) -> bool:
    """判断 session 中本条输入是否属于需先登录的 RSL/RQ。"""
    s = line.strip()
    if not s:
        return False
    if s.lower().startswith("logon"):
        return False
    if s.startswith("<"):
        try:
            root = ET.fromstring(s)
        except ET.ParseError:
            return False
        return root.tag in _XML_ROOT_TAGS_REQUIRE_LOGON
    parts = s.split()
    return bool(parts) and parts[0].lower() in _SESSION_FIRST_TOKEN_REQUIRES_LOGON


def _build_attr_xml(tag: str, attrs: dict[str, object | None]) -> str:
    parts: list[str] = [f"<{tag}"]
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, bool):
            s = "true" if v else "false"
        else:
            s = str(v)
        if s == "":
            continue
        parts.append(f' {k}="{_xml_escape(s)}"')
    parts.append("/>")
    return "".join(parts)


def _engine_with_heartbeat(args: argparse.Namespace, on_msg=None):
    if on_msg is None:
        on_msg = lambda msg: print(msg)
    heartbeat_interval = getattr(args, "heartbeat", 0.0) or 0.0
    heartbeat_idle = getattr(args, "heartbeat_idle_timeout", 0.0) or 0.0
    return AsyncTcpCommunicationEngine(
        request_culture=args.culture,
        encoding=args.encoding,
        on_message=on_msg,
        heartbeat_interval_s=heartbeat_interval,
        heartbeat_idle_timeout_s=heartbeat_idle,
    )


async def _run_tcp(args: argparse.Namespace) -> int:
    def on_msg(msg: str) -> None:
        print(msg)

    engine = _engine_with_heartbeat(args, on_msg=on_msg)
    ok = await engine.connect(args.host, args.port)
    if not ok:
        print("连接失败。", file=sys.stderr)
        return 2

    try:
        cmd = args.tcp_cmd
        if cmd in _TCP_CMDS_REQUIRE_LOGON:
            u = (getattr(args, "username", None) or "").strip()
            p = getattr(args, "password", None) or ""
            if not u or not p:
                print(
                    "Remote Sample Login / Remote Query 命令需要 --username 与 --password（先 Logon，成功后再执行）。",
                    file=sys.stderr,
                )
                return 1
            if not await _tcp_ensure_logon(engine, u, p, timeout_s=args.timeout):
                return 1

        if args.tcp_cmd == "version":
            resp = await engine.send_xml("<Version/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "supported-cultures":
            resp = await engine.send_xml("<SupportedCultures/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "instrument-info":
            resp = await engine.send_xml("<InstrumentInfo/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "remote-control-state":
            resp = await engine.send_xml("<RemoteControlState/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "ambient":
            xml = f'<Ambient Key="{args.key}"/>'
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "ambients":
            resp = await engine.send_xml("<Ambients/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "automation-status":
            xml = _build_attr_xml("AutomationStatus", {"Id": args.id})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "available-logs":
            resp = await engine.send_xml("<AvailableLogs/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "counter":
            xml = _build_attr_xml("Counter", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "counters":
            resp = await engine.send_xml("<Counters/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "detectors":
            resp = await engine.send_xml("<Detectors/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "double-value":
            xml = _build_attr_xml("DoubleValue", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "double-values":
            resp = await engine.send_xml("<DoubleValues/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "exception-directory":
            resp = await engine.send_xml("<ExceptionDirectory/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "field":
            xml = _build_attr_xml("Field", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "fields":
            resp = await engine.send_xml("<Fields/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "filters":
            resp = await engine.send_xml("<Filters/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "gas-state":
            resp = await engine.send_xml("<GasState/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "log-data":
            loop = asyncio.get_running_loop()
            if not (args.log or args.start or args.end) and args.max_entries is None:
                try:
                    args.log, args.start, args.end, args.max_entries = await _session_prompt_log_data(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
            if not args.log:
                print("LogData 需要 --log。", file=sys.stderr)
                return 1
            xml = _build_attr_xml(
                "LogData",
                {
                    "Log": args.log,
                    "Start": args.start,
                    "End": args.end,
                    "MaxEntries": args.max_entries if args.max_entries is not None else 1000,
                },
            )
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "log-directory":
            resp = await engine.send_xml("<LogDirectory/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "message-history":
            resp = await engine.send_xml("<MessageHistory/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "method":
            xml = _build_attr_xml("Method", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "methods":
            resp = await engine.send_xml("<Methods/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "mondo-data":
            loop = asyncio.get_running_loop()
            if not (args.pic_id or args.start or args.end) and args.max_entries is None:
                try:
                    args.pic_id, args.start, args.end, args.max_entries = await _session_prompt_mondo_data(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
            if not args.pic_id:
                print("MondoData 需要 --pic-id。", file=sys.stderr)
                return 1
            xml = _build_attr_xml(
                "MondoData",
                {
                    "PicId": args.pic_id,
                    "Start": args.start,
                    "End": args.end,
                    "MaxEntries": args.max_entries if args.max_entries is not None else 1000,
                },
            )
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "mondo-directory":
            resp = await engine.send_xml("<MondoDirectory/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "next-to-analyze":
            resp = await engine.send_xml("<NextToAnalyze/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "prerequisite":
            xml = _build_attr_xml("Prerequisite", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "prerequisites":
            resp = await engine.send_xml("<Prerequisites/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "qc-status":
            xml = _build_attr_xml("QCStatus", {"MethodKey": args.method_key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "rep-detail":
            xml = _build_attr_xml("RepDetail", {"SetKey": args.set_key, "Tag": args.tag})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "rep-plot":
            xml = _build_attr_xml("RepPlot", {"SetKey": args.set_key, "Tag": args.tag})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "report":
            xml = _build_attr_xml("Report", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "reports":
            resp = await engine.send_xml("<Reports/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "sequence":
            xml = _build_attr_xml("Sequence", {"Name": args.name})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "sequences":
            resp = await engine.send_xml("<Sequences/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "set":
            xml = _build_attr_xml("Set", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "set-keys-ex2":
            resp = await engine.send_xml("<SetKeysEx2/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "set-reps":
            loop = asyncio.get_running_loop()
            if args.key is None and args.include_detail_data is None and args.tag is None:
                try:
                    args.key, args.include_detail_data, args.tag = await _session_prompt_set_reps(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
            if not args.key:
                print("SetReps 需要 --key。", file=sys.stderr)
                return 1
            xml = _build_attr_xml(
                "SetReps",
                {
                    "Key": args.key,
                    "IncludeDetailData": args.include_detail_data if args.include_detail_data is not None else False,
                    "Tag": args.tag if args.tag is not None else -1,
                },
            )
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "sets":
            loop = asyncio.get_running_loop()
            if args.filter_key is None and args.number is None and args.start_at is None:
                try:
                    args.filter_key, args.number, args.start_at = await _session_prompt_sets(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
            xml = _build_attr_xml(
                "Sets",
                {
                    "FilterKey": args.filter_key or "",
                    "Number": args.number if args.number is not None else 10,
                    "StartAt": args.start_at if args.start_at is not None else -1,
                },
            )
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "solenoid":
            xml = _build_attr_xml("Solenoid", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "solenoids":
            resp = await engine.send_xml("<Solenoids/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "standard":
            xml = _build_attr_xml("Standard", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "standards":
            resp = await engine.send_xml("<Standards/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "status":
            loop = asyncio.get_running_loop()
            if args.include_gauges is None and args.include_system_check_results is None and args.include_leak_check_results is None:
                try:
                    (
                        args.include_gauges,
                        args.include_system_check_results,
                        args.include_leak_check_results,
                    ) = await _session_prompt_status(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
            xml = _build_attr_xml(
                "Status",
                {
                    "IncludeGauges": True if args.include_gauges is None else args.include_gauges,
                    "IncludeSystemCheckResults": True
                    if args.include_system_check_results is None
                    else args.include_system_check_results,
                    "IncludeLeakCheckResults": True
                    if args.include_leak_check_results is None
                    else args.include_leak_check_results,
                },
            )
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "string-value":
            xml = _build_attr_xml("StringValue", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "string-values":
            resp = await engine.send_xml("<StringValues/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "switch":
            xml = _build_attr_xml("Switch", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "switches":
            resp = await engine.send_xml("<Switches/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "system-parameters":
            resp = await engine.send_xml("<SystemParameters/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "transport":
            xml = _build_attr_xml("Transport", {"Key": args.key})
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "transports":
            resp = await engine.send_xml("<Transports/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "valve-states":
            resp = await engine.send_xml("<ValveStates/>", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "logon":
            xml = _logon_xml(args.user, args.password)
            resp = await engine.send_xml(xml, cookie="LOGON", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "logoff":
            resp = await engine.send_xml("<Logoff/>", cookie="LOGOFF", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "send":
            resp = await engine.send_xml(args.xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "last-remote-added-sets":
            resp = await engine.send_xml("<LastRemoteAddedSets/>", cookie="LastRemoteAddedSets", timeout_s=args.timeout)
            if resp:
                print(resp)
        elif args.tcp_cmd == "add-samples":
            xml = getattr(args, "xml", "") or ""
            if not xml:
                loop = asyncio.get_running_loop()
                try:
                    xml = await _prompt_add_samples(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    return 1
                except ValueError as e:
                    print(e, file=sys.stderr)
                    return 1
            resp = await engine.send_xml(xml, timeout_s=args.timeout)
            if resp:
                print(resp)
        else:
            raise RuntimeError("未知 TCP 子命令")
    finally:
        await engine.disconnect()

    return 0


# Session 中“需逐行提示参数”的占位 cookie，由 _run_tcp_session 识别并走交互流程
SESSION_PROMPT_LOGON = "__PROMPT_LOGON__"
SESSION_PROMPT_ADD_SAMPLES = "__PROMPT_ADD_SAMPLES__"
SESSION_PROMPT_LOG_DATA = "__PROMPT_LOG_DATA__"
SESSION_PROMPT_MONDO_DATA = "__PROMPT_MONDO_DATA__"
SESSION_PROMPT_SET_REPS = "__PROMPT_SET_REPS__"
SESSION_PROMPT_SETS = "__PROMPT_SETS__"
SESSION_PROMPT_STATUS = "__PROMPT_STATUS__"


def _session_parse_line(line: str) -> tuple[str, str] | None:
    """解析一行输入为 (xml, cookie)。无法解析时返回 None。"""
    line = line.strip()
    if not line:
        return None
    lower = line.lower()
    if lower in ("exit", "quit", "q"):
        return None  # 由调用方判断
    if lower == "version":
        return "<Version/>", ""
    if lower == "supported-cultures":
        return "<SupportedCultures/>", ""
    if lower == "instrument-info":
        return "<InstrumentInfo/>", ""
    if lower == "remote-control-state":
        return "<RemoteControlState/>", ""
    if lower == "logoff":
        return "<Logoff/>", "LOGOFF"
    if lower == "last-remote-added-sets":
        return "<LastRemoteAddedSets/>", "LastRemoteAddedSets"
    if lower == "ambients":
        return "<Ambients/>", ""
    if lower.startswith("ambient "):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1]:
            return f'<Ambient Key="{parts[1]}"/>', ""
        return None
    # Remote Query：无参数
    if lower in (
        "available-logs",
        "counters",
        "detectors",
        "double-values",
        "exception-directory",
        "fields",
        "filters",
        "gas-state",
        "log-directory",
        "message-history",
        "methods",
        "mondo-directory",
        "next-to-analyze",
        "prerequisites",
        "reports",
        "sequences",
        "set-keys-ex2",
        "solenoids",
        "standards",
        "string-values",
        "switches",
        "system-parameters",
        "transports",
        "valve-states",
    ):
        tag = "".join([p[:1].upper() + p[1:] for p in lower.split("-")])
        return f"<{tag}/>", ""
    # Remote Query：需要参数（尽量支持 session 里直接在命令后跟值）
    if lower.startswith("automation-status"):
        parts = line.split(None, 1)
        if len(parts) == 1:
            return "<AutomationStatus/>", ""
        return _build_attr_xml("AutomationStatus", {"Id": parts[1]}), ""
    for name, tag in (
        ("counter", "Counter"),
        ("double-value", "DoubleValue"),
        ("field", "Field"),
        ("method", "Method"),
        ("prerequisite", "Prerequisite"),
        ("report", "Report"),
        ("set", "Set"),
        ("solenoid", "Solenoid"),
        ("standard", "Standard"),
        ("string-value", "StringValue"),
        ("switch", "Switch"),
        ("transport", "Transport"),
    ):
        if lower.startswith(name + " "):
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1]:
                return _build_attr_xml(tag, {"Key": parts[1]}), ""
            return None
    if lower.startswith("qc-status "):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1]:
            return _build_attr_xml("QCStatus", {"MethodKey": parts[1]}), ""
        return None
    if lower.startswith("rep-detail "):
        parts = line.split(None, 2)
        if len(parts) == 3:
            return _build_attr_xml("RepDetail", {"SetKey": parts[1], "Tag": parts[2]}), ""
        return None
    if lower.startswith("rep-plot "):
        parts = line.split(None, 2)
        if len(parts) == 3:
            return _build_attr_xml("RepPlot", {"SetKey": parts[1], "Tag": parts[2]}), ""
        return None
    if lower.startswith("sequence "):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1]:
            return _build_attr_xml("Sequence", {"Name": parts[1]}), ""
        return None
    # 可“只输入命令名→逐行提示参数”的 Remote Query
    if lower == "log-data":
        return "", SESSION_PROMPT_LOG_DATA
    if lower == "mondo-data":
        return "", SESSION_PROMPT_MONDO_DATA
    if lower == "set-reps":
        return "", SESSION_PROMPT_SET_REPS
    if lower == "sets":
        return "", SESSION_PROMPT_SETS
    if lower == "status":
        return "", SESSION_PROMPT_STATUS
    if lower == "logon":
        # 无参数：由 session 逐行提示 User / Password
        return "", SESSION_PROMPT_LOGON
    if lower.startswith("logon "):
        parts = line.split(None, 2)
        if len(parts) >= 3:
            return (
                f"<Logon><User>{_xml_escape(parts[1])}</User>"
                f"<Password>{_xml_escape(parts[2])}</Password></Logon>"
            ), "LOGON"
        return None
    if lower == "add-samples":
        # 无参数：由 session 逐行提示收集 XML
        return "", SESSION_PROMPT_ADD_SAMPLES
    if line.startswith("<"):
        return line, ""
    return None


async def _session_read_line(loop: asyncio.AbstractEventLoop, prompt: str) -> str:
    """在 session 中从 stdin 读一行（在 executor 中执行 input，避免阻塞事件循环）。"""
    return (await loop.run_in_executor(None, lambda: input(prompt))).strip()


async def _prompt_int(
    loop: asyncio.AbstractEventLoop,
    prompt: str,
    *,
    min_value: int | None = None,
) -> int:
    """带重试的整数输入。"""
    while True:
        text = await _session_read_line(loop, prompt)
        try:
            value = int(text)
        except ValueError:
            print("请输入整数。", file=sys.stderr)
            continue
        if min_value is not None and value < min_value:
            print(f"必须 >= {min_value}。", file=sys.stderr)
            continue
        return value


async def _session_prompt_logon(loop: asyncio.AbstractEventLoop) -> tuple[str, str]:
    """逐行提示输入 User、Password，返回 (xml, cookie)。"""
    user = await _session_read_line(loop, "User: ")
    password = await _session_read_line(loop, "Password: ")
    xml = _logon_xml(user, password)
    return xml, "LOGON"


async def _prompt_optional(loop: asyncio.AbstractEventLoop, prompt: str, default: str = "") -> str:
    value = await _session_read_line(loop, f"{prompt}{' [' + default + ']' if default != '' else ''}: ")
    return value if value != "" else default


async def _prompt_bool(loop: asyncio.AbstractEventLoop, prompt: str, default: bool) -> bool:
    while True:
        d = "true" if default else "false"
        value = await _session_read_line(loop, f"{prompt} [true/false] ({d}): ")
        if value == "":
            return default
        try:
            return _bool_from_arg(value)
        except argparse.ArgumentTypeError:
            print("请输入 true 或 false。", file=sys.stderr)


async def _session_prompt_log_data(loop: asyncio.AbstractEventLoop) -> tuple[str, str, str, int]:
    log = await _prompt_optional(loop, "Log", "")
    if not log:
        raise ValueError("Log 不能为空。")
    start = await _prompt_optional(loop, "Start (可空，GMT: MM/DD/YYYY HH:MM:SS.fffff)", "")
    end = await _prompt_optional(loop, "End (可空，GMT: MM/DD/YYYY HH:MM:SS.fffff)", "")
    max_entries = await _prompt_int(loop, "MaxEntries", min_value=1)
    return log, start, end, max_entries


async def _session_prompt_mondo_data(loop: asyncio.AbstractEventLoop) -> tuple[str, str, str, int]:
    pic_id = await _prompt_optional(loop, "PicId", "")
    if not pic_id:
        raise ValueError("PicId 不能为空。")
    start = await _prompt_optional(loop, "Start (可空，GMT: MM/DD/YYYY HH:MM:SS.fffff)", "")
    end = await _prompt_optional(loop, "End (可空，GMT: MM/DD/YYYY HH:MM:SS.fffff)", "")
    max_entries = await _prompt_int(loop, "MaxEntries", min_value=1)
    return pic_id, start, end, max_entries


async def _session_prompt_set_reps(loop: asyncio.AbstractEventLoop) -> tuple[str, bool, int]:
    key = await _prompt_optional(loop, "Key", "")
    if not key:
        raise ValueError("Key 不能为空。")
    include_detail = await _prompt_bool(loop, "IncludeDetailData", False)
    tag_text = await _prompt_optional(loop, "Tag（可选，-1 表示全部）", "-1")
    try:
        tag = int(tag_text)
    except ValueError:
        raise ValueError("Tag 必须是整数。")
    return key, include_detail, tag


async def _session_prompt_sets(loop: asyncio.AbstractEventLoop) -> tuple[str, int, int]:
    filter_key = await _prompt_optional(loop, "FilterKey（可空）", "")
    number = await _prompt_int(loop, "Number", min_value=1)
    start_at_text = await _prompt_optional(loop, "StartAt（-1 表示最新 N 条）", "-1")
    try:
        start_at = int(start_at_text)
    except ValueError:
        raise ValueError("StartAt 必须是整数。")
    return filter_key, number, start_at


async def _session_prompt_status(loop: asyncio.AbstractEventLoop) -> tuple[bool, bool, bool]:
    include_gauges = await _prompt_bool(loop, "IncludeGauges", True)
    include_system = await _prompt_bool(loop, "IncludeSystemCheckResults", True)
    include_leak = await _prompt_bool(loop, "IncludeLeakCheckResults", True)
    return include_gauges, include_system, include_leak


async def _prompt_sample_type(loop: asyncio.AbstractEventLoop) -> str:
    """提示输入合法的 SampleType。"""
    valid = {"Blank", "GasDose", "Sample", "Standard"}
    while True:
        value = await _session_read_line(
            loop,
            "SampleType [Blank/GasDose/Sample/Standard]: ",
        )
        if value in valid:
            return value
        print("SampleType 只能是 Blank/GasDose/Sample/Standard 之一。", file=sys.stderr)


async def _prompt_replicates(loop: asyncio.AbstractEventLoop, count: int) -> list[dict[str, str]]:
    """提示输入若干 Replicate 的字段信息。"""
    reps: list[dict[str, str]] = []
    for idx in range(1, count + 1):
        print(f"Replicate {idx}/{count}:", file=sys.stderr)
        mass = await _session_read_line(loop, "  Mass: ")
        if not mass:
            raise ValueError("Mass 不能为空。")
        comments = await _session_read_line(loop, "  Comments (可空): ")
        location = await _session_read_line(loop, "  Location (可空): ")
        reps.append(
            {
                "Mass": mass,
                "Comments": comments,
                "Location": location,
            }
        )
    return reps


async def _prompt_add_samples(loop: asyncio.AbstractEventLoop) -> str:
    """通过问答方式构建 AddSamples XML。"""
    # 先选择是现有 Set 还是新 Set
    while True:
        choice = await _session_read_line(
            loop,
            "Add Replicates to an [e]xisting Set or Add Replicates to a [n]ew Set? (e/n): ",
        )
        choice = (choice or "").strip().lower()
        if choice in ("e", "n"):
            break
        print("请输入 'e'（existing）或 'n'（new）。", file=sys.stderr)

    if choice == "e":
        # 现有 set：SetKey + Replicates
        set_key = await _session_read_line(loop, "SetKey: ")
        if not set_key:
            raise ValueError("SetKey 不能为空。")
        count = await _prompt_int(loop, "Replicates 数量: ", min_value=1)
        reps = await _prompt_replicates(loop, count)

        parts: list[str] = ["<AddSamples>", f"<SetKey>{set_key}</SetKey>", "<Replicates>"]
        for r in reps:
            parts.append("<Replicate>")
            parts.append(f"<Field Id=\"Mass\">{r['Mass']}</Field>")
            if r["Comments"]:
                parts.append(f"<Field Id=\"Comments\">{r['Comments']}</Field>")
            if r["Location"]:
                parts.append(f"<Field Id=\"Location\">{r['Location']}</Field>")
            parts.append("</Replicate>")
        parts.append("</Replicates>")
        parts.append("</AddSamples>")
        return "".join(parts)

    # 新 set：SampleType + 若干字段 + Replicates
    sample_type = await _prompt_sample_type(loop)
    name = ""
    if sample_type in ("Blank", "Sample"):
        name = await _session_read_line(loop, "Name (可空，对 Blank/Sample 有效): ")
    description = await _session_read_line(loop, "Description (可空): ")
    method_key = await _session_read_line(loop, "MethodKey: ")
    if not method_key:
        raise ValueError("MethodKey 不能为空。")

    standard_key = ""
    if sample_type in ("GasDose", "Standard"):
        standard_key = await _session_read_line(loop, "StandardKey: ")
        if not standard_key:
            raise ValueError("StandardKey 不能为空（对 GasDose/Standard 必填）。")

    count = await _prompt_int(loop, "Replicates 数量: ", min_value=1)
    reps = await _prompt_replicates(loop, count)

    parts = ["<AddSamples>", "<Set>"]
    parts.append(f"<Field Id=\"SampleType\">{sample_type}</Field>")
    if name and sample_type in ("Blank", "Sample"):
        parts.append(f"<Field Id=\"Name\">{name}</Field>")
    if description:
        parts.append(f"<Field Id=\"Description\">{description}</Field>")
    parts.append(f"<Field Id=\"MethodKey\">{method_key}</Field>")
    if standard_key and sample_type in ("GasDose", "Standard"):
        parts.append(f"<Field Id=\"StandardKey\">{standard_key}</Field>")
    parts.append("</Set>")
    parts.append("<Replicates>")
    for r in reps:
        parts.append("<Replicate>")
        parts.append(f"<Field Id=\"Mass\">{r['Mass']}</Field>")
        if r["Comments"]:
            parts.append(f"<Field Id=\"Comments\">{r['Comments']}</Field>")
        if r["Location"]:
            parts.append(f"<Field Id=\"Location\">{r['Location']}</Field>")
        parts.append("</Replicate>")
    parts.append("</Replicates>")
    parts.append("</AddSamples>")
    return "".join(parts)


async def _session_prompt_add_samples(loop: asyncio.AbstractEventLoop) -> tuple[str, str]:
    """Session 模式下，通过问答方式构建 AddSamples XML，返回 (xml, cookie)。"""
    xml = await _prompt_add_samples(loop)
    return xml, ""


def _is_heartbeat_message(msg: str) -> bool:
    """判断是否为心跳响应，避免在交互中打印干扰输入。"""
    s = (msg or "").strip()
    if not s.startswith("<"):
        return False
    try:
        root = ET.fromstring(s)
        return root is not None and root.tag == "Heartbeat"
    except ET.ParseError:
        return False


async def _run_tcp_session(args: argparse.Namespace) -> int:
    def on_msg(msg: str) -> None:
        if _is_heartbeat_message(msg):
            return
        print("[消息]", msg)

    def on_disconnected() -> None:
        print("连接已断开（心跳超时或网络异常）。", file=sys.stderr)

    engine = AsyncTcpCommunicationEngine(
        request_culture=args.culture,
        encoding=args.encoding,
        on_message=on_msg,
        on_disconnected=on_disconnected,
        heartbeat_interval_s=args.heartbeat,
        heartbeat_idle_timeout_s=args.heartbeat_idle_timeout,
    )
    ok = await engine.connect(args.host, args.port)
    if not ok:
        print("连接失败。", file=sys.stderr)
        return 2

    timeout_s = getattr(args, "timeout", 30.0)
    session_logged_in = False
    su = (getattr(args, "username", None) or "").strip()
    sp = getattr(args, "password", None) or ""
    if su and sp:
        if not await _tcp_ensure_logon(engine, su, sp, timeout_s=timeout_s):
            await engine.disconnect()
            return 1
        session_logged_in = True

    print(
        "长连接已建立。输入命令或 XML 发送，空行/exit/quit 退出。心跳: {}s，空闲超时: {}。".format(
            args.heartbeat, args.heartbeat_idle_timeout or "不检测"
        ),
        file=sys.stderr,
    )
    print(
        "命令: version, supported-cultures, instrument-info, remote-control-state, ambients, ambient KEY, "
        "automation-status [ID], available-logs, counter KEY, counters, detectors, double-value KEY, double-values, "
        "exception-directory, field KEY, fields, filters, gas-state, log-data, log-directory, message-history, "
        "method KEY, methods, mondo-data, mondo-directory, next-to-analyze, prerequisite KEY, prerequisites, "
        "qc-status METHODKEY, rep-detail SETKEY TAG, rep-plot SETKEY TAG, report KEY, reports, sequence NAME, sequences, "
        "set KEY, set-keys-ex2, set-reps, sets, solenoid KEY, solenoids, standard KEY, standards, status, "
        "string-value KEY, string-values, switch KEY, switches, system-parameters, transport KEY, transports, valve-states, "
        "logon [USER PASSWORD], logoff, add-samples, last-remote-added-sets；或直接输入 XML。"
        " logon/add-samples 可只输入命令名，将逐行提示参数。",
        file=sys.stderr,
    )
    loop = asyncio.get_running_loop()

    try:
        while engine.connected:
            try:
                line = await loop.run_in_executor(None, lambda: input("> "))
            except (EOFError, KeyboardInterrupt):
                break
            stripped = line.strip()
            if not stripped or stripped.lower() in ("exit", "quit", "q"):
                break
            parsed = _session_parse_line(line)
            if parsed is None:
                if stripped:
                    print("未知命令。输入: version, instrument-info, logon [USER PWD], logoff, add-samples, last-remote-added-sets, 或 XML（如 <AddSamples>...</AddSamples>）。", file=sys.stderr)
                continue
            xml, cookie = parsed
            # 需逐行提示参数的命令
            if cookie == SESSION_PROMPT_LOGON:
                try:
                    xml, cookie = await _session_prompt_logon(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_ADD_SAMPLES:
                try:
                    xml, cookie = await _session_prompt_add_samples(loop)
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_LOG_DATA:
                try:
                    log, start, end, max_entries = await _session_prompt_log_data(loop)
                    xml, cookie = (
                        _build_attr_xml("LogData", {"Log": log, "Start": start, "End": end, "MaxEntries": max_entries}),
                        "",
                    )
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_MONDO_DATA:
                try:
                    pic_id, start, end, max_entries = await _session_prompt_mondo_data(loop)
                    xml, cookie = (
                        _build_attr_xml("MondoData", {"PicId": pic_id, "Start": start, "End": end, "MaxEntries": max_entries}),
                        "",
                    )
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_SET_REPS:
                try:
                    key, include_detail, tag = await _session_prompt_set_reps(loop)
                    xml, cookie = (
                        _build_attr_xml("SetReps", {"Key": key, "IncludeDetailData": include_detail, "Tag": tag}),
                        "",
                    )
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_SETS:
                try:
                    filter_key, number, start_at = await _session_prompt_sets(loop)
                    xml, cookie = (
                        _build_attr_xml("Sets", {"FilterKey": filter_key, "Number": number, "StartAt": start_at}),
                        "",
                    )
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            elif cookie == SESSION_PROMPT_STATUS:
                try:
                    include_gauges, include_system, include_leak = await _session_prompt_status(loop)
                    xml, cookie = (
                        _build_attr_xml(
                            "Status",
                            {
                                "IncludeGauges": include_gauges,
                                "IncludeSystemCheckResults": include_system,
                                "IncludeLeakCheckResults": include_leak,
                            },
                        ),
                        "",
                    )
                except (EOFError, KeyboardInterrupt):
                    print("已取消。", file=sys.stderr)
                    continue
                except ValueError as e:
                    print(e, file=sys.stderr)
                    continue
            try:
                resp = await engine.send_xml(xml, cookie=cookie or "", timeout_s=timeout_s)
                if resp:
                    print(resp)
            except RuntimeError as e:
                print("发送失败（连接已断）:", e, file=sys.stderr)
                break
            except asyncio.TimeoutError:
                print("等待响应超时。", file=sys.stderr)
            except Exception as e:
                print("发送失败:", e, file=sys.stderr)
    except asyncio.CancelledError:
        pass
    finally:
        await engine.disconnect()
    return 0


def _run_http(args: argparse.Namespace) -> int:
    req = WebRequestor()
    user_lab_info = build_user_lab_info_xml(args.user, args.password, args.labname, args.labkey)

    if args.http_cmd == "instruments":
        uri = req.create_uri("RegisteredInstruments.aspx", parameters=None, server=args.server)
        xml = req.make_request(uri, user_lab_info)
        print(xml)
        return 0

    if args.http_cmd == "request":
        uri = req.create_uri_for_instrument(
            "RequestData.aspx",
            instrument_id=args.instrument_id,
            parameters=None,
            server=args.server,
        )
        post = build_post_data(user_lab_info, args.command_xml)
        xml = req.make_request(uri, post)
        print(xml)
        return 0

    raise RuntimeError("未知 HTTP 子命令")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cornerstone-cli", description="CornerstoneRemoteControlClient Python 重写版（通信层 + CLI）")
    sub = parser.add_subparsers(dest="mode", required=True)

    # TCP
    p_tcp = sub.add_parser("tcp", help="直连 TCP（长度前缀帧）")

    # 让参数出现在子命令之后也能解析，例如：tcp version --host ... --port ...
    tcp_common = argparse.ArgumentParser(add_help=False)
    tcp_common.add_argument("--host", required=True)
    tcp_common.add_argument("--port", type=int, required=True)
    tcp_common.add_argument("--culture", default="en-US")
    tcp_common.add_argument("--encoding", type=_encoding_from_arg, default=TcpEncoding.utf16)
    tcp_common.add_argument("--timeout", type=float, default=30.0, help="等待响应超时（秒）")

    tcp_requires_logon = argparse.ArgumentParser(add_help=False)
    tcp_requires_logon.add_argument(
        "--username",
        required=True,
        help="用户名：先发送 Logon，应答为 ErrorCode=0 且 ErrorMessage=Success 后再执行本命令",
    )
    tcp_requires_logon.add_argument("--password", required=True, help="密码")

    tcp_sub = p_tcp.add_subparsers(dest="tcp_cmd", required=True)
    p_session = tcp_sub.add_parser(
        "session",
        help="长连接，用心跳检查连接状态，按 Ctrl+C 退出",
        parents=[tcp_common],
    )
    p_session.add_argument(
        "--username",
        default="",
        help="可选：连接成功后自动 Logon；与 --password 同时提供时用于 Remote Sample Login / Remote Query",
    )
    p_session.add_argument(
        "--password",
        default="",
        help="可选：与 --username 配对",
    )
    p_session.add_argument(
        "--heartbeat",
        type=float,
        default=5.0,
        metavar="SEC",
        help="心跳间隔（秒），发送 <Heartbeat/>；0 表示不发送",
    )
    p_session.add_argument(
        "--heartbeat-idle-timeout",
        type=float,
        default=0.0,
        metavar="SEC",
        help="空闲超时（秒），超过未收到任何数据则断开；0 表示不检测",
    )
    tcp_sub.add_parser("version", help="发送 <Version/>", parents=[tcp_common])
    tcp_sub.add_parser("supported-cultures", help="发送 <SupportedCultures/>", parents=[tcp_common])
    tcp_sub.add_parser("instrument-info", help="发送 <InstrumentInfo/>", parents=[tcp_common])
    tcp_sub.add_parser("remote-control-state", help="发送 <RemoteControlState/>", parents=[tcp_common])
    tcp_sub.add_parser(
        "ambients",
        help="Remote Query：检索仪器上所有 ambient 的摘要信息（对应 <Ambients/>）",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_ambient = tcp_sub.add_parser(
        "ambient",
        help="Remote Query：根据指定 Key 检索单个 ambient 的详细信息（对应 <Ambient Key=\"...\"/>）",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_ambient.add_argument(
        "--key",
        required=True,
        help="ambient 的唯一 Key（可省略前导 0）",
    )

    # Remote Query Commands
    p_automation_status = tcp_sub.add_parser("automation-status", help="Remote Query：<AutomationStatus Id=\"...\"/>（Id 可选）", parents=[tcp_common, tcp_requires_logon])
    p_automation_status.add_argument("--id", required=False, default="", help="可选：指定 automation id，仅返回该 id 的状态")

    tcp_sub.add_parser("available-logs", help="Remote Query：<AvailableLogs/>", parents=[tcp_common, tcp_requires_logon])

    p_counter = tcp_sub.add_parser("counter", help="Remote Query：<Counter Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_counter.add_argument("--key", required=True, help="Counter 的 Key（可省略前导 0）")
    tcp_sub.add_parser("counters", help="Remote Query：<Counters/>", parents=[tcp_common, tcp_requires_logon])

    tcp_sub.add_parser("detectors", help="Remote Query：<Detectors/>", parents=[tcp_common, tcp_requires_logon])

    p_double_value = tcp_sub.add_parser("double-value", help="Remote Query：<DoubleValue Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_double_value.add_argument("--key", required=True, help="DoubleValue 的 Key")
    tcp_sub.add_parser("double-values", help="Remote Query：<DoubleValues/>", parents=[tcp_common, tcp_requires_logon])

    tcp_sub.add_parser("exception-directory", help="Remote Query：<ExceptionDirectory/>", parents=[tcp_common, tcp_requires_logon])

    p_field = tcp_sub.add_parser("field", help="Remote Query：<Field Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_field.add_argument("--key", required=True, help="Field 的 Key（可省略前导 0）")
    tcp_sub.add_parser("fields", help="Remote Query：<Fields/>", parents=[tcp_common, tcp_requires_logon])

    tcp_sub.add_parser("filters", help="Remote Query：<Filters/>", parents=[tcp_common, tcp_requires_logon])
    tcp_sub.add_parser("gas-state", help="Remote Query：<GasState/>", parents=[tcp_common, tcp_requires_logon])

    p_log_data = tcp_sub.add_parser(
        "log-data",
        help="Remote Query：<LogData .../>（可省略参数走逐行提示）",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_log_data.add_argument("--log", required=False, default="", help="log 标识（必填；省略则逐行提示）")
    p_log_data.add_argument("--start", required=False, default="", help="开始时间（GMT: MM/DD/YYYY HH:MM:SS.fffff，可空）")
    p_log_data.add_argument("--end", required=False, default="", help="结束时间（GMT: MM/DD/YYYY HH:MM:SS.fffff，可空）")
    p_log_data.add_argument("--max-entries", dest="max_entries", type=int, required=False, default=None, help="最大条数（默认 1000；省略且无其它参数时逐行提示）")

    tcp_sub.add_parser("log-directory", help="Remote Query：<LogDirectory/>", parents=[tcp_common, tcp_requires_logon])
    tcp_sub.add_parser("message-history", help="Remote Query：<MessageHistory/>", parents=[tcp_common, tcp_requires_logon])

    p_method = tcp_sub.add_parser("method", help="Remote Query：<Method Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_method.add_argument("--key", required=True, help="Method 的 Key（可省略前导 0）")
    tcp_sub.add_parser("methods", help="Remote Query：<Methods/>", parents=[tcp_common, tcp_requires_logon])

    p_mondo_data = tcp_sub.add_parser(
        "mondo-data",
        help="Remote Query：<MondoData .../>（可省略参数走逐行提示）",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_mondo_data.add_argument("--pic-id", dest="pic_id", required=False, default="", help="PicId（必填；省略则逐行提示）")
    p_mondo_data.add_argument("--start", required=False, default="", help="开始时间（GMT: MM/DD/YYYY HH:MM:SS.fffff，可空）")
    p_mondo_data.add_argument("--end", required=False, default="", help="结束时间（GMT: MM/DD/YYYY HH:MM:SS.fffff，可空）")
    p_mondo_data.add_argument("--max-entries", dest="max_entries", type=int, required=False, default=None, help="最大条数（默认 1000；省略且无其它参数时逐行提示）")

    tcp_sub.add_parser("mondo-directory", help="Remote Query：<MondoDirectory/>", parents=[tcp_common, tcp_requires_logon])
    tcp_sub.add_parser("next-to-analyze", help="Remote Query：<NextToAnalyze/>", parents=[tcp_common, tcp_requires_logon])

    p_prerequisite = tcp_sub.add_parser("prerequisite", help="Remote Query：<Prerequisite Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_prerequisite.add_argument("--key", required=True, help="Prerequisite 的 Key")
    tcp_sub.add_parser("prerequisites", help="Remote Query：<Prerequisites/>", parents=[tcp_common, tcp_requires_logon])

    p_qc_status = tcp_sub.add_parser("qc-status", help="Remote Query：<QCStatus MethodKey=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_qc_status.add_argument("--method-key", dest="method_key", required=True, help="MethodKey（可省略前导 0）")

    p_rep_detail = tcp_sub.add_parser("rep-detail", help="Remote Query：<RepDetail SetKey=\"...\" Tag=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_rep_detail.add_argument("--set-key", dest="set_key", required=True)
    p_rep_detail.add_argument("--tag", required=True)

    p_rep_plot = tcp_sub.add_parser("rep-plot", help="Remote Query：<RepPlot SetKey=\"...\" Tag=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_rep_plot.add_argument("--set-key", dest="set_key", required=True)
    p_rep_plot.add_argument("--tag", required=True)

    p_report = tcp_sub.add_parser("report", help="Remote Query：<Report Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_report.add_argument("--key", required=True)
    tcp_sub.add_parser("reports", help="Remote Query：<Reports/>", parents=[tcp_common, tcp_requires_logon])

    p_sequence = tcp_sub.add_parser("sequence", help="Remote Query：<Sequence Name=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_sequence.add_argument("--name", required=True, help="Sequence 名称")
    tcp_sub.add_parser("sequences", help="Remote Query：<Sequences/>", parents=[tcp_common, tcp_requires_logon])

    p_set = tcp_sub.add_parser("set", help="Remote Query：<Set Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_set.add_argument("--key", required=True)
    tcp_sub.add_parser("set-keys-ex2", help="Remote Query：<SetKeysEx2/>", parents=[tcp_common, tcp_requires_logon])

    p_set_reps = tcp_sub.add_parser("set-reps", help="Remote Query：<SetReps .../>（可只输入命令名逐行提示）", parents=[tcp_common, tcp_requires_logon])
    p_set_reps.add_argument("--key", required=False, default=None, help="Set 的 Key（省略可逐行提示）")
    p_set_reps.add_argument("--include-detail-data", dest="include_detail_data", type=_bool_from_arg, required=False, default=None, help="true/false（默认 false；省略可逐行提示）")
    p_set_reps.add_argument("--tag", required=False, type=int, default=None, help="可选 rep tag（默认 -1 表示全部；省略可逐行提示）")

    p_sets = tcp_sub.add_parser("sets", help="Remote Query：<Sets .../>（可只输入命令名逐行提示）", parents=[tcp_common, tcp_requires_logon])
    p_sets.add_argument("--filter-key", dest="filter_key", required=False, default=None, help="FilterKey（可空）")
    p_sets.add_argument("--number", required=False, type=int, default=None, help="Number（默认 10；省略可逐行提示）")
    p_sets.add_argument("--start-at", dest="start_at", required=False, type=int, default=None, help="StartAt（默认 -1；省略可逐行提示）")

    p_solenoid = tcp_sub.add_parser("solenoid", help="Remote Query：<Solenoid Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_solenoid.add_argument("--key", required=True)
    tcp_sub.add_parser("solenoids", help="Remote Query：<Solenoids/>", parents=[tcp_common, tcp_requires_logon])

    p_standard = tcp_sub.add_parser("standard", help="Remote Query：<Standard Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_standard.add_argument("--key", required=True)
    tcp_sub.add_parser("standards", help="Remote Query：<Standards/>", parents=[tcp_common, tcp_requires_logon])

    p_status = tcp_sub.add_parser("status", help="Remote Query：<Status .../>（可只输入命令名逐行提示）", parents=[tcp_common, tcp_requires_logon])
    p_status.add_argument("--include-gauges", dest="include_gauges", type=_bool_from_arg, required=False, default=None, help="true/false（默认 true；省略可逐行提示）")
    p_status.add_argument("--include-system-check-results", dest="include_system_check_results", type=_bool_from_arg, required=False, default=None, help="true/false（默认 true；省略可逐行提示）")
    p_status.add_argument("--include-leak-check-results", dest="include_leak_check_results", type=_bool_from_arg, required=False, default=None, help="true/false（默认 true；省略可逐行提示）")

    p_string_value = tcp_sub.add_parser("string-value", help="Remote Query：<StringValue Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_string_value.add_argument("--key", required=True)
    tcp_sub.add_parser("string-values", help="Remote Query：<StringValues/>", parents=[tcp_common, tcp_requires_logon])

    p_switch = tcp_sub.add_parser("switch", help="Remote Query：<Switch Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_switch.add_argument("--key", required=True)
    tcp_sub.add_parser("switches", help="Remote Query：<Switches/>", parents=[tcp_common, tcp_requires_logon])

    tcp_sub.add_parser("system-parameters", help="Remote Query：<SystemParameters/>", parents=[tcp_common, tcp_requires_logon])

    p_transport = tcp_sub.add_parser("transport", help="Remote Query：<Transport Key=\"...\"/>", parents=[tcp_common, tcp_requires_logon])
    p_transport.add_argument("--key", required=True)
    tcp_sub.add_parser("transports", help="Remote Query：<Transports/>", parents=[tcp_common, tcp_requires_logon])

    tcp_sub.add_parser("valve-states", help="Remote Query：<ValveStates/>", parents=[tcp_common, tcp_requires_logon])

    p_logon = tcp_sub.add_parser("logon", help="发送 <Logon/>（Cookie=LOGON）", parents=[tcp_common])
    p_logon.add_argument("--user", required=True)
    p_logon.add_argument("--password", required=True)

    tcp_sub.add_parser("logoff", help="发送 <Logoff/>（Cookie=LOGOFF）", parents=[tcp_common])

    tcp_sub.add_parser(
        "last-remote-added-sets",
        help="Remote Sample Login：获取最近 RSL 添加的 set 的 key",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_add_samples = tcp_sub.add_parser(
        "add-samples",
        help="Remote Sample Login：向现有 set 或新 set 添加 replicates（可直接提供完整 AddSamples XML，或省略 --xml 走交互问答）",
        parents=[tcp_common, tcp_requires_logon],
    )
    p_add_samples.add_argument("--xml", required=False, help='完整 <AddSamples>...</AddSamples> XML，若省略则通过问答逐步生成')

    p_send = tcp_sub.add_parser("send", help="发送自定义 XML（自动注入 Cookie/Culture）", parents=[tcp_common])
    p_send.add_argument("--xml", required=True)

    # HTTP
    p_http = sub.add_parser("http", help="通过云端 HTTP 转发")

    http_common = argparse.ArgumentParser(add_help=False)
    http_common.add_argument("--server", default="remote.lecosoftware.com")
    http_common.add_argument("--user", required=True)
    http_common.add_argument("--password", required=True)
    http_common.add_argument("--labname", required=True)
    http_common.add_argument("--labkey", required=True)

    http_sub = p_http.add_subparsers(dest="http_cmd", required=True)
    http_sub.add_parser("instruments", help="RegisteredInstruments.aspx", parents=[http_common])

    p_req = http_sub.add_parser("request", help="RequestData.aspx（带 instrument id）", parents=[http_common])
    p_req.add_argument("--instrument-id", required=True)
    p_req.add_argument("--command-xml", required=True, help='例如 "<InstrumentInfo/>"')

    args = parser.parse_args(argv)

    if args.mode == "tcp":
        if getattr(args, "tcp_cmd", None) == "session":
            return asyncio.run(_run_tcp_session(args))
        return asyncio.run(_run_tcp(args))
    if args.mode == "http":
        return _run_http(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

