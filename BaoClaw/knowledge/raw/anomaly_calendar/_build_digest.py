# -*- coding: utf-8 -*-
"""One-shot: JSON gas events -> markdown digest + naming notes."""
import collections
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

RAW = pathlib.Path(__file__).resolve().parent
KNOWLEDGE = RAW.parents[0]  # .../knowledge
MD_DIR = KNOWLEDGE / "markdown" / "anomaly_calendar"

data = json.loads(
    (RAW / "wps_rili_chemical_gas_events_2025-2026.json").read_text(encoding="utf-8")
)
events = data["events"]
code_re = re.compile(r"(?i)(?<![A-Za-z0-9])((?:GC|GO|CS|ONH?|QV)\s*\d+)")
pri1_re = re.compile(
    r"(?i)(?:GC\s*[6-9]|GO\s*[6-9]|CS\s*844|ON\s*H?\s*836|二炼钢)"
)

codes = collections.Counter()
labs = collections.Counter()
for e in events:
    text = e["summary"] + " " + (e.get("description") or "")
    for m in code_re.findall(text):
        codes[re.sub(r"\s+", "", m).upper()] += 1
    loc = e.get("location") or ""
    if loc:
        labs[loc] += 1
    for lab in ("一炼钢", "二炼钢", "电炉", "原料"):
        if lab in text or lab in loc:
            labs[lab] += 1

print("count", len(events))
print("TOP codes:")
for k, v in codes.most_common(40):
    print(f"  {k}: {v}")
print("TOP labs:")
for k, v in labs.most_common(20):
    print(f"  {k}: {v}")

CST = timezone(timedelta(hours=8))


def fmt(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d %H:%M")


lines = [
    "# 化学区域 · 气体分析仪相关异常日历（摘录）",
    "",
    "- 来源：WPS日历「化学区域」（teamId=67579784）",
    "- 范围：2025-01 ~ 2026-08",
    f"- 粗筛条数：{len(events)}（化学区域全量事件中按代号/气体类关键词过滤）",
    "",
    "## 检索与称呼约定",
    "",
    "- **可采集机型（优先）**：CS844（碳硫）、ON(H)836（氧氮氢）。",
    "- **日志称呼不稳定**：多用实验室代号，少写型号。",
    "- **二炼钢实验室**：已联机设备（GC6/7/8、GO6/7/9 等）——与编排 `lab-2lg` 对应。",
    "- **电炉实验室**：GC2、GC3、GC4、ON1、ON2 等。",
    "- **一炼钢实验室**：CS2、CS3、ON2、ONH2 等。",
    "- **泛称**：氧氮 / 碳硫 / 定氢 / 气体 —— 往往不带型号，可能是上一代 CS600、TC(H)600；仍保留作参考。",
    "- 标签策略：保留原文；另标注 lab_hint / code_hint / model_hint（未知则 unknown 或 legacy-possible）。",
    "",
    "## 代号频次（摘要）",
    "",
]
for k, v in codes.most_common(30):
    lines.append(f"- `{k}` × {v}")
lines += ["", "## 事件列表（按时间）", ""]

for e in sorted(events, key=lambda x: x.get("orgStartTime") or 0):
    text = e["summary"] + " " + (e.get("description") or "")
    tier = (
        "P1"
        if pri1_re.search(text) or "二炼钢" in (e.get("location") or "")
        else "P2"
    )
    codes_found = sorted(
        {re.sub(r"\s+", "", m).upper() for m in code_re.findall(text)}
    )
    lines.append(f"### {e.get('date', '')} · {e['summary']}")
    lines.append(
        f"- taskId: `{e['taskId']}` · tier: `{tier}` · 地点: "
        f"{e.get('location') or '—'} · 记录人: {e.get('nickName') or '—'}"
    )
    lines.append(
        f"- 时间: {fmt(e.get('orgStartTime'))} ~ {fmt(e.get('orgEndTime'))}"
    )
    if codes_found:
        lines.append("- 代号: " + ", ".join(codes_found))
    desc = (e.get("description") or "").strip()
    if desc:
        lines.append(f"- 描述: {desc}")
    lines.append("")

MD_DIR.mkdir(parents=True, exist_ok=True)
md_path = MD_DIR / "wps_rili_chemical_gas_2025-2026.md"
md_path.write_text("\n".join(lines), encoding="utf-8")
print("wrote", md_path, "bytes", md_path.stat().st_size)
