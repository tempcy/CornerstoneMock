# -*- coding: utf-8 -*-
"""Process legacy anomaly calendars: Synology ICS + pre-2019 weekly ET/XLS reports.

Outputs under knowledge/raw|markdown/anomaly_calendar/.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import xlrd

RAW = pathlib.Path(__file__).resolve().parent
KNOWLEDGE = RAW.parents[1]  # .../knowledge
MD_DIR = KNOWLEDGE / "markdown" / "anomaly_calendar"
CST = timezone(timedelta(hours=8))

# Prefer collectable models; keep legacy/aliases for reference.
CODE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:GC|GO|CS|ONH?|QV|TF|GH|TC|EMIA)\s*\d+[A-Za-z]?)"
)
GAS_HINT_RE = re.compile(
    r"(?i)(?:\b(?:GC|GO|CS|ONH?|QV|TF|GH|TC|EMIA)\s*\d*"
    r"|氧氮|碳硫|定氢|气体|管式炉|加样器|检漏|坩埚|炉头"
    r"|CS\s*844|ON\s*H?\s*836|CS\s*600|CS\s*444|CS\s*744"
    r"|TC\s*H?\s*600|TC[- ]?436|LECO|Cornerstone|EMIA)"
)
PRI1_RE = re.compile(
    r"(?i)(?:GC\s*[6-9]|GO\s*[6-9]|CS\s*844|ON\s*H?\s*836|二炼钢)"
)

# ICS calendars that primarily hold gas / mixed gas content.
ICS_PRIMARY = {
    "气体类区域.ics",
    "理化及气体分析区域.ics",
    "My Calendar.ics",  # early Synology personal mirror; many gas entries
}
ICS_SCAN_ALL = True  # also scan other ICS for scattered gas items


def unfold_ics(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n").replace("\r", "\n"))


def unescape_ics(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def parse_ics_dt(prop: str) -> str | None:
    """Return ISO-like local datetime string from DTSTART/DTEND property line value."""
    if not prop:
        return None
    # VALUE=DATE:20200101 or TZID=...:20200101T120000
    m = re.search(r":(\d{8})(T\d{6})?", prop)
    if not m:
        return None
    d, t = m.group(1), m.group(2)
    if t:
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]} {t[1:3]}:{t[3:5]}:{t[5:7]}"
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


def parse_vevents(path: pathlib.Path) -> list[dict[str, Any]]:
    text = unfold_ics(path.read_text(encoding="utf-8", errors="replace"))
    cal_m = re.search(r"X-WR-CALNAME:(.+)", text)
    cal_name = unescape_ics(cal_m.group(1)) if cal_m else path.stem
    events: list[dict[str, Any]] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.S):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or ":" not in line:
                continue
            key, val = line.split(":", 1)
            key0 = key.split(";", 1)[0].upper()
            # keep first occurrence of core fields; DESCRIPTION may be long
            if key0 in fields and key0 != "DESCRIPTION":
                continue
            fields[key0] = val if key0 != "DESCRIPTION" else fields.get(
                "DESCRIPTION", ""
            ) + val
            # store raw DTSTART line for TZID parsing
            if key0 in ("DTSTART", "DTEND"):
                fields[key0 + "_RAW"] = line
        summary = unescape_ics(fields.get("SUMMARY", ""))
        if not summary:
            continue
        # skip Synology junk / placeholder distant dates
        start = parse_ics_dt(fields.get("DTSTART_RAW") or fields.get("DTSTART", ""))
        if start and start[:4] in {"1901", "1940", "1949"}:
            continue
        desc = unescape_ics(fields.get("DESCRIPTION", ""))
        loc = unescape_ics(fields.get("LOCATION", ""))
        events.append(
            {
                "uid": fields.get("UID", ""),
                "calendar": cal_name,
                "source_file": path.name,
                "summary": summary,
                "description": desc,
                "location": loc,
                "start": start,
                "end": parse_ics_dt(fields.get("DTEND_RAW") or fields.get("DTEND", "")),
                "date": (start or "")[:10],
            }
        )
    return events


def is_gas_event(ev: dict[str, Any], *, force: bool = False) -> bool:
    if force:
        return True
    blob = f"{ev.get('summary','')} {ev.get('description','')} {ev.get('location','')}"
    return bool(GAS_HINT_RE.search(blob))


def codes_in(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", "", m).upper() for m in CODE_RE.findall(text or "")})


def tier_of(text: str, location: str = "") -> str:
    if PRI1_RE.search(text or "") or "二炼钢" in (location or ""):
        return "P1"
    return "P2"


def split_bullets(text: str) -> list[str]:
    if not text or not str(text).strip():
        return []
    t = str(text).replace("\r\n", "\n").replace("\r", "\n")
    # Prefer filled-circle bullets
    if "●" in t:
        parts = [p.strip() for p in re.split(r"●", t) if p.strip()]
        return parts
    # fallback: paragraphs
    parts = [p.strip() for p in re.split(r"\n+", t) if p.strip()]
    return parts


def excel_date_label(cell_val: Any, book: xlrd.Book) -> str:
    if isinstance(cell_val, float) and cell_val > 30000:
        try:
            dt = xlrd.xldate_as_datetime(cell_val, book.datemode)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return str(cell_val)
    return str(cell_val).strip()


def parse_weekly_gas_sheet(path: pathlib.Path) -> list[dict[str, Any]]:
    """Extract 气体类 rows from 区域每周设备管理汇总."""
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as e:
        return [{"_error": f"{path.name}: {type(e).__name__}: {e}"}]

    if "区域每周设备管理汇总" not in book.sheet_names():
        return [{"_error": f"{path.name}: missing sheet 区域每周设备管理汇总"}]

    sh = book.sheet_by_name("区域每周设备管理汇总")
    # year hint from filename
    ym = re.search(r"(20\d{2})", path.name)
    year_hint = int(ym.group(1)) if ym else None

    out: list[dict[str, Any]] = []
    current_week = ""
    current_range = ""
    for r in range(1, sh.nrows):
        cells = [sh.cell_value(r, c) for c in range(min(sh.ncols, 8))]
        # normalize
        c0 = cells[0] if len(cells) > 0 else ""
        c1 = cells[1] if len(cells) > 1 else ""
        c2 = str(cells[2]).strip() if len(cells) > 2 else ""
        # region may sit in col2 or col0 depending on merged layout
        region = ""
        for cand in (c2, str(c0).strip(), str(c1).strip()):
            if cand in {"气体类", "光谱类", "X射线类", "热工类", "理化类", "其它", "其他"}:
                region = cand
                break
        # week header often on first category row
        if isinstance(c0, float) and 1 <= c0 <= 60:
            current_week = str(int(c0))
        if isinstance(c1, str) and ("月" in c1 and "日" in c1):
            current_range = c1.strip()
        elif isinstance(c1, float) and c1 > 30000:
            current_range = excel_date_label(c1, book)

        if region != "气体类":
            continue

        # find fault / tech columns: typically last text-heavy cells
        texts = [str(x).strip() for x in cells if isinstance(x, str) and len(str(x).strip()) > 4]
        # exclude region label
        texts = [t for t in texts if t not in {"气体类", "光谱类", "X射线类"}]
        fault = ""
        tech = ""
        # Prefer columns by header positions 5/6 when present
        if sh.ncols >= 7:
            fault = str(sh.cell_value(r, 5)).strip()
            tech = str(sh.cell_value(r, 6)).strip() if sh.ncols >= 7 else ""
        if not fault and texts:
            fault = texts[0]
            tech = texts[1] if len(texts) > 1 else ""

        week_label = f"{year_hint or ''}W{current_week}".strip("W") if current_week else ""
        if year_hint and current_week:
            week_label = f"{year_hint}-W{current_week}"

        for kind, blob in (("异常与故障", fault), ("技术管理", tech)):
            for bullet in split_bullets(blob):
                out.append(
                    {
                        "source_file": path.name,
                        "source_type": "weekly_report",
                        "year": year_hint,
                        "week": current_week,
                        "week_label": week_label,
                        "date_range": current_range,
                        "region": "气体类",
                        "kind": kind,
                        "summary": bullet[:120].replace("\n", " "),
                        "description": bullet,
                        "location": "",
                        "codes": codes_in(bullet),
                        "tier": tier_of(bullet),
                    }
                )
    return out


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path, "bytes", path.stat().st_size)


def events_to_md(title: str, meta_lines: list[str], events: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""] + [f"- {m}" for m in meta_lines] + ["", "## 事件列表", ""]
    def sort_key(e: dict[str, Any]):
        return (
            e.get("date") or e.get("week_label") or e.get("date_range") or "",
            e.get("start") or "",
            e.get("summary") or "",
        )

    for e in sorted(events, key=sort_key):
        head = e.get("date") or e.get("week_label") or e.get("date_range") or "?"
        lines.append(f"### {head} · {e.get('summary','')[:100]}")
        bits = []
        if e.get("calendar"):
            bits.append(f"日历: {e['calendar']}")
        if e.get("source_file"):
            bits.append(f"来源: `{e['source_file']}`")
        if e.get("tier"):
            bits.append(f"tier: `{e['tier']}`")
        if e.get("location"):
            bits.append(f"地点: {e['location']}")
        if e.get("kind"):
            bits.append(f"栏目: {e['kind']}")
        if bits:
            lines.append("- " + " · ".join(bits))
        if e.get("start"):
            lines.append(f"- 时间: {e.get('start')} ~ {e.get('end') or '—'}")
        codes = e.get("codes") or codes_in(
            f"{e.get('summary','')} {e.get('description','')}"
        )
        if codes:
            lines.append("- 代号: " + ", ".join(codes))
        desc = (e.get("description") or "").strip()
        if desc and desc != e.get("summary"):
            lines.append(f"- 描述: {desc}")
        elif desc and not e.get("summary"):
            lines.append(f"- 描述: {desc}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    MD_DIR.mkdir(parents=True, exist_ok=True)

    # ---- ICS ----
    ics_all: list[dict[str, Any]] = []
    ics_gas: list[dict[str, Any]] = []
    per_cal: dict[str, dict[str, int]] = {}

    for path in sorted(RAW.glob("*.ics")):
        evs = parse_vevents(path)
        gas_evs = []
        for e in evs:
            e["codes"] = codes_in(f"{e['summary']} {e['description']}")
            e["tier"] = tier_of(
                f"{e['summary']} {e['description']}", e.get("location", "")
            )
            e["source_type"] = "synology_ics"
            # 气体类区域：日历本身即气体域，全收；其它 ICS：关键词/代号筛选
            if path.name == "气体类区域.ics":
                keep = True
            elif ICS_SCAN_ALL:
                keep = is_gas_event(e)
            else:
                keep = False
            ics_all.append(e)
            if keep:
                gas_evs.append(e)
                ics_gas.append(e)
        years = Counter((e.get("date") or "")[:4] for e in gas_evs if e.get("date"))
        per_cal[path.name] = {
            "events_total": len(evs),
            "gas_kept": len(gas_evs),
            "years": dict(sorted(years.items())),
        }
        print(f"ICS {path.name}: total={len(evs)} gas_kept={len(gas_evs)} years={dict(sorted(years.items()))}")

    write_json(
        RAW / "synology_ics_gas_events.json",
        {
            "note": "群晖 ICS 导出；气体类区域全收；其它日历按气体关键词/代号筛选",
            "per_calendar": per_cal,
            "count": len(ics_gas),
            "events": ics_gas,
        },
    )
    (MD_DIR / "synology_ics_gas_events.md").write_text(
        events_to_md(
            "群晖日历 · 气体相关异常（ICS）",
            [
                "来源：Synology/DAViCal 导出的 .ics（区域调整后分散）",
                f"保留条数：{len(ics_gas)}",
                "主键历：气体类区域（全量）；其余日历仅气体关键词命中",
                "可采集优先：CS844 / ON(H)836；代号与泛称仍保留作参考",
            ],
            ics_gas,
        ),
        encoding="utf-8",
    )
    print("wrote", MD_DIR / "synology_ics_gas_events.md")

    # ---- Weekly ET/XLS ----
    report_files = [
        "仪器专业组设备综合管理报表2013.et",
        "仪器专业组设备综合管理报表2014.xls",
        "仪器专业组设备综合管理报表2015.et",
        "仪器专业组设备综合管理报表2016.et",
        "仪器专业组设备综合管理报表2017.et",
        "仪器专业组设备综合管理报表_汇总(2018).xls",
        "计量检定室区域设备状态管理表(2012).et",
    ]
    weekly_items: list[dict[str, Any]] = []
    report_status: list[dict[str, Any]] = []
    for name in report_files:
        path = RAW / name
        if not path.exists():
            report_status.append({"file": name, "status": "missing"})
            continue
        items = parse_weekly_gas_sheet(path)
        if items and "_error" in items[0]:
            report_status.append({"file": name, "status": "error", "detail": items[0]["_error"]})
            print("REPORT", items[0]["_error"])
            continue
        # empty workbook
        try:
            book = xlrd.open_workbook(str(path))
            empty = all(
                book.sheet_by_index(i).nrows == 0 for i in range(book.nsheets)
            )
        except Exception as e:
            report_status.append({"file": name, "status": "error", "detail": str(e)})
            continue
        if empty:
            report_status.append({"file": name, "status": "empty_workbook"})
            print(f"REPORT {name}: empty workbook")
            continue
        weekly_items.extend(items)
        report_status.append({"file": name, "status": "ok", "gas_bullets": len(items)})
        print(f"REPORT {name}: gas_bullets={len(items)}")

    write_json(
        RAW / "weekly_report_gas_pre2019.json",
        {
            "note": "2019年前仪器专业组周报「气体类」条目拆分；加密/空表见 status",
            "status": report_status,
            "count": len(weekly_items),
            "events": weekly_items,
        },
    )
    (MD_DIR / "weekly_report_gas_pre2019.md").write_text(
        events_to_md(
            "事报表 · 气体类周报条目（约 2016–2018）",
            [
                "来源：仪器专业组设备综合管理报表（.et/.xls）→ 区域每周设备管理汇总 → 气体类",
                f"拆分条数：{len(weekly_items)}",
                "2012/2013 空表；2014/2015 工作簿加密（暂未能解密）",
                "条目多为周汇总 bullet，日期为周区间而非精确到日",
            ],
            weekly_items,
        ),
        encoding="utf-8",
    )
    print("wrote", MD_DIR / "weekly_report_gas_pre2019.md")

    # code histogram
    codes = Counter()
    for e in ics_gas + weekly_items:
        for c in e.get("codes") or codes_in(
            f"{e.get('summary','')} {e.get('description','')}"
        ):
            codes[c] += 1
    print("TOP codes:", codes.most_common(25))

    # update README snippet file
    summary = {
        "ics": per_cal,
        "weekly_status": report_status,
        "ics_gas_count": len(ics_gas),
        "weekly_gas_count": len(weekly_items),
        "top_codes": codes.most_common(30),
    }
    write_json(RAW / "legacy_import_summary.json", summary)


if __name__ == "__main__":
    main()
