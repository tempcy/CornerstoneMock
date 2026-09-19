#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scan LECO 844/836-style PDF: detect large-graphic pages, render PNGs by chapter,
and write per-manual figure catalogs + master index.

Layout (knowledge/):
  figures/manuals/<manual_id>/<chapter_slug>/fig_….png
  catalogs/manuals/<manual_id>/chNN_figures.md + INDEX.md
  catalogs/INDEX.md  (all manuals)

Usage:
  python extract_figures_by_chapter.py --pdf raw/manuals/836….pdf \\
    --toc-md markdown/manuals/ON836_….md --doc-id ON836_Instruction_Manual_v3.3_2024-06
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parent

# Footer: "Illustrations 844 Series 10–16" / "2–19 836 Series Installations"
CH_FOOTER_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z /&-]{2,40}?)\s+"
    r"(?P<series>\d{3})\s+Series\s+"
    r"(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<p>\d+)",
    re.I,
)
CH_FOOTER_RE2 = re.compile(
    r"(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<p>\d+)\s+"
    r"(?P<series>\d{3})\s+Series\s+"
    r"(?P<name>[A-Za-z][A-Za-z /&-]{2,40})",
    re.I,
)
SERIES_TOKEN_RE = re.compile(r"\b(?P<series>\d{3})\s+Series\b", re.I)
FIG_RE = re.compile(
    r"Figure\s*(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<n>\d+)\s*(?P<title>[^\n\r]{0,120})",
    re.I,
)
TOC_LINE_RE = re.compile(
    r"Figure\s*\d+\s*[\u2013\-\u2014]\s*\d+.+\.{3,}",
    re.I,
)

CHAPTER_SLUG = {
    1: "01_introduction",
    2: "02_installations",
    3: "03_software",
    4: "04_diagnostics",
    5: "05_instrument",
    6: "06_maintenance",
    7: "07_theory",
    8: "08_service_ops",
    9: "09_service",
    10: "10_illustrations",
    11: "11_schematics",
    12: "12_index",
    0: "00_frontmatter",
}

TOC_FIG_RE = re.compile(
    r"^Figure\s+(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<n>\d+)\s+"
    r"(?P<title>.+?)(?:\.{2,}|\s{2,})\s*\d+\s*[\u2013\-\u2014]",
    re.I | re.M,
)


def load_toc_titles(md_path: Path | None) -> dict[str, str]:
    if not md_path or not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8", errors="replace")
    head = "\n".join(text.splitlines()[:450])
    out: dict[str, str] = {}
    for m in TOC_FIG_RE.finditer(head):
        fid = f"{m.group('ch')}-{m.group('n')}"
        title = re.sub(r"\s+", " ", m.group("title")).strip(" .\t")
        if title and fid not in out:
            out[fid] = title
    return out


def clean_title(raw: str, fid: str, toc: dict[str, str]) -> str:
    t = (raw or "").strip(" .\t,")
    junk = (
        not t
        or t.lower() in {"following", "page", "of"}
        or t.startswith(",")
        or re.match(r"^(following|page)\b", t, re.I)
        or len(t) < 3
        or (" " not in t and len(t) > 12)
    )
    if fid in toc:
        if junk or len(toc[fid]) > len(t) + 5:
            return toc[fid]
    return t if not junk else toc.get(fid, t)


@dataclass
class FigureHit:
    pdf_page: int
    chapter: int
    chapter_name: str
    figure_id: str
    title: str
    text_len: int
    reason: str
    image_rel: str = ""
    extra_figures: list[str] = field(default_factory=list)


def page_text(pdf: pdfium.PdfDocument, index0: int) -> str:
    t = pdf[index0].get_textpage().get_text_bounded() or ""
    return t.replace("\r", "\n")


def detect_chapter(text: str, prev_ch: int, prev_name: str) -> tuple[int, str]:
    head = text[:800]
    tail = text[-500:] if len(text) > 500 else text
    blob = head + "\n" + tail
    for rx in (CH_FOOTER_RE, CH_FOOTER_RE2):
        m = rx.search(blob)
        if m:
            ch = int(m.group("ch"))
            name = re.sub(r"\s+", " ", m.group("name")).strip(" .-")
            if 1 <= ch <= 20:
                return ch, name
    fm = FIG_RE.search(text[:600])
    if fm:
        return int(fm.group("ch")), prev_name or CHAPTER_SLUG.get(int(fm.group("ch")), "unknown")
    return prev_ch, prev_name


def extract_figures(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in FIG_RE.finditer(text):
        fid = f"{m.group('ch')}-{m.group('n')}"
        title = (m.group("title") or "").strip(" .\t–-")
        if re.search(r"\.{4,}", title) or re.search(r"\d+\s*[\u2013\-]\s*\d+\s*$", title):
            title = re.split(r"\.{2,}|\s+\d+\s*[\u2013\-]", title)[0].strip(" .\t")
        out.append((fid, title))
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for fid, title in out:
        if fid in seen:
            continue
        seen.add(fid)
        uniq.append((fid, title))
    return uniq


def is_large_graphic_page(text: str, figures: list[tuple[str, str]]) -> tuple[bool, str]:
    body = text.strip()
    n = len(body)
    low = body.lower()
    if "intentionally left blank" in low:
        return False, ""
    if len(TOC_LINE_RE.findall(body)) >= 3:
        return False, ""
    if body.count("....") >= 5:
        return False, ""
    if n <= 120:
        return True, "sparse_text<=120"
    if n <= 400 and figures:
        return True, "sparse_text<=400+figure"
    if n <= 700 and figures and n / max(body.count("\n") + 1, 1) < 80:
        return True, "caption_heavy<=700"
    if figures and n <= 1200:
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if not lines:
            return True, "empty"
        captionish = 0
        for ln in lines:
            if FIG_RE.match(ln) or re.match(r"^\d+\s*[\u2013\-]\s*\d+", ln):
                captionish += 1
            elif SERIES_TOKEN_RE.search(ln):
                captionish += 1
            elif len(ln) < 60 and not ln.endswith("."):
                captionish += 1
        if captionish / len(lines) >= 0.65:
            return True, "mostly_captions"
    return False, ""


def slug_chapter(ch: int, name: str) -> str:
    base = CHAPTER_SLUG.get(ch)
    if base:
        return base
    safe = re.sub(r"[^a-z0-9]+", "_", (name or "chapter").lower()).strip("_")[:40]
    return f"{ch:02d}_{safe or 'chapter'}"


def slug_manual(name: str) -> str:
    s = Path(name).stem if name.lower().endswith((".pdf", ".md")) else name
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.UNICODE).strip("._")
    return s[:120] or "manual"


def resolve_manual_id(doc_id: str | None, toc_md: Path | None, pdf_path: Path) -> str:
    if doc_id:
        return slug_manual(doc_id)
    if toc_md and toc_md.name:
        return slug_manual(toc_md.name)
    return slug_manual(pdf_path.name)


def rebuild_master_index(cat_manuals: Path) -> None:
    """Rewrite catalogs/INDEX.md listing every manuals/<id>/INDEX.md."""
    lines = [
        "# 仪器知识库 — 图录总索引",
        "",
        "> 模型：按图录/正文做检索与定位。详细读图（装配/线路）交给人。",
        "",
        "| 类别 | 手册 | 图录 |",
        "|------|------|------|",
    ]
    if cat_manuals.is_dir():
        for d in sorted(cat_manuals.iterdir()):
            if not d.is_dir():
                continue
            idx = d / "INDEX.md"
            if idx.is_file():
                lines.append(f"| manuals | `{d.name}` | [`INDEX.md`](manuals/{d.name}/INDEX.md) |")
    lines.extend(
        [
            "",
            "## 其它类别（预留）",
            "",
            "- `procedures/` — 手顺书",
            "- `fault_cases/` — 故障案例",
            "- `anomaly_calendar/` — 设备异常日历",
            "",
        ]
    )
    (cat_manuals.parent / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT)
    ap.add_argument("--scale", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--toc-md", type=Path, default=None)
    ap.add_argument("--doc-id", type=str, default="")
    ap.add_argument("--catalog-only", action="store_true")
    args = ap.parse_args()

    pdf_path = args.pdf
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    toc_md = args.toc_md
    if toc_md is None:
        # Heuristic: markdown/manuals/<doc-id or similar>.md
        guess = args.out / "markdown" / "manuals"
        if args.doc_id:
            cand = guess / f"{slug_manual(args.doc_id)}.md"
            if cand.is_file():
                toc_md = cand

    manual_id = resolve_manual_id(args.doc_id or None, toc_md, pdf_path)
    fig_root = args.out / "figures" / "manuals" / manual_id
    cat_root = args.out / "catalogs" / "manuals" / manual_id
    fig_root.mkdir(parents=True, exist_ok=True)
    cat_root.mkdir(parents=True, exist_ok=True)

    toc = load_toc_titles(toc_md)
    print(f"TOC titles loaded: {len(toc)} from {toc_md}")
    print(f"figures: figures/manuals/{manual_id}/")
    print(f"catalogs: catalogs/manuals/{manual_id}/")

    pdf = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(pdf)
    limit = args.limit or n_pages
    skip_render = args.dry_run or args.catalog_only
    print(f"PDF pages={n_pages} scanning={limit} scale={args.scale} dry_run={skip_render}")

    hits: list[FigureHit] = []
    prev_ch, prev_name = 0, "FrontMatter"

    for i in range(limit):
        text = page_text(pdf, i)
        ch, name = detect_chapter(text, prev_ch, prev_name)
        if ch:
            prev_ch, prev_name = ch, name or prev_name
        figures = extract_figures(text)
        ok, reason = is_large_graphic_page(text, figures)
        if not ok:
            continue

        if figures:
            fid, title = figures[0]
            title = clean_title(title, fid, toc)
            extras = []
            for a, b in figures[1:]:
                bt = clean_title(b, a, toc)
                extras.append(f"{a}" + (f" {bt}" if bt else ""))
        else:
            fid = f"p{i+1}"
            title = (name or "Graphic") + f" (PDF p.{i+1})"
            extras = []
            if not ch:
                ch, name = prev_ch, prev_name

        ch = ch or prev_ch or 0
        name = name or prev_name
        slug = slug_chapter(ch, name)
        if figures and figures[0][0].count("-") == 1:
            fname = f"fig_{fid.replace('-', '_')}.png"
        else:
            fname = f"page_{i+1:04d}.png"
        dest_dir = fig_root / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fname
        alt = dest_dir / f"fig_{fid.replace('-', '_')}_p{i+1:04d}.png"
        if skip_render and alt.is_file():
            dest = alt
        elif dest.exists() and not skip_render:
            dest = alt
        elif skip_render and not dest.is_file() and alt.is_file():
            dest = alt

        rel = dest.relative_to(args.out).as_posix()
        hit = FigureHit(
            pdf_page=i + 1,
            chapter=ch,
            chapter_name=name,
            figure_id=fid if figures else f"page-{i+1}",
            title=title or "",
            text_len=len(text.strip()),
            reason=reason,
            image_rel=rel,
            extra_figures=extras,
        )
        hits.append(hit)

        if not skip_render:
            bitmap = pdf[i].render(scale=args.scale)
            bitmap.to_pil().save(dest, optimize=True)
        if (len(hits) % 25) == 0:
            print(f"  … {len(hits)} graphic pages (at PDF p.{i+1})")

    by_ch: dict[int, list[FigureHit]] = defaultdict(list)
    for h in hits:
        by_ch[h.chapter].append(h)

    md_rel = ""
    if toc_md and toc_md.is_file():
        try:
            md_rel = toc_md.relative_to(args.out).as_posix()
        except ValueError:
            md_rel = toc_md.as_posix()

    index_lines = [
        f"# {manual_id} — 大图图录总索引",
        "",
        f"源 PDF：`{pdf_path.name}`",
        f"图片目录：`figures/manuals/{manual_id}/`",
        f"检出大图页：**{len(hits)}** / 扫描 {limit}",
        "",
        "> **读图约定**：模型按图录标题检索定位；装配/线路等详细读图交给人。",
        "",
        "| 章 | 名称 | 大图页数 | 图录 |",
        "|----|------|----------|------|",
    ]

    for ch in sorted(by_ch.keys()):
        rows = by_ch[ch]
        name = rows[0].chapter_name
        cat_name = f"ch{ch:02d}_figures.md"
        cat_path = cat_root / cat_name
        lines = [
            f"# 第 {ch} 章 {name} — 大图图录",
            "",
            f"共 **{len(rows)}** 页大图（按 PDF 页序）。",
            "",
            "| Figure | 标题 | PDF 页 | 图片 | 判定 |",
            "|--------|------|--------|------|------|",
        ]
        for h in rows:
            title = h.title.replace("|", "\\|")
            # catalogs/manuals/<id>/chXX.md → figures/manuals/<id>/...
            img = ""
            if h.image_rel:
                link = Path("../../../") / h.image_rel
                img = f"[`{Path(h.image_rel).name}`]({link.as_posix()})"
            lines.append(
                f"| {h.figure_id} | {title} | {h.pdf_page} | {img} | {h.reason} |"
            )
            if h.extra_figures:
                lines.append(f"|  | _(同页另有: {'; '.join(h.extra_figures)})_ |  |  |  |")
        lines.append("")
        cat_path.write_text("\n".join(lines), encoding="utf-8")
        index_lines.append(
            f"| {ch} | {name} | {len(rows)} | [`{cat_name}`]({cat_name}) |"
        )
        print(f"catalog ch{ch:02d}: {len(rows)} -> {cat_path.relative_to(args.out)}")

    index_lines.extend(
        [
            "",
            "## 使用说明",
            "",
            f"- 正文 Markdown：`{md_rel or '(provide --toc-md)'}`",
            "- 模型：检索图录图题与正文；不要默认视觉读完全部 PNG。",
            "- 人：对装配/布线/原理图打开对应 PNG 细读。",
            "",
        ]
    )
    index_path = cat_root / "INDEX.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    meta_path = cat_root / "figures_meta.json"
    meta_path.write_text(
        json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    rebuild_master_index(args.out / "catalogs" / "manuals")
    print(f"Wrote {index_path.relative_to(args.out)}")
    print(f"Wrote {meta_path.relative_to(args.out)}")
    print(f"Done: {len(hits)} graphic pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
