#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scan CS844 (or similar) PDF: detect large-graphic pages, render PNGs by chapter,
and write per-chapter figure catalogs + master index.

Usage:
  python extract_figures_by_chapter.py
  python extract_figures_by_chapter.py --pdf path/to.pdf --scale 1.5
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
DEFAULT_PDF = ROOT / "raw" / (
    "844 Series CarbonSulfur Analyzer Instruction Manual Version 3.3.x June 2024.pdf"
)

# Footer / header: "Illustrations 844 Series 10–16" or "2–19 844 Series Installations"
CH_FOOTER_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z /&-]{2,40}?)\s+844 Series\s+"
    r"(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<p>\d+)",
    re.I,
)
CH_FOOTER_RE2 = re.compile(
    r"(?P<ch>\d+)\s*[\u2013\-\u2014]\s*(?P<p>\d+)\s+844 Series\s+"
    r"(?P<name>[A-Za-z][A-Za-z /&-]{2,40})",
    re.I,
)
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
    3: "03_software",  # Analysis chapter; keep folder name stable
    4: "04_diagnostics",  # Settings; keep folder name stable
    5: "05_instrument",
    6: "06_maintenance",
    7: "07_theory",
    8: "08_service_ops",  # Diagnostics; keep folder name stable
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
    """Figure id -> title from List of Illustrations in converted Markdown."""
    if not md_path or not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8", errors="replace")
    # Prefer front-matter TOC (first ~400 lines) to avoid body noise
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
        or (" " not in t and len(t) > 12)  # glued OCR like ReferenceCard1of2
    )
    if fid in toc:
        if junk or len(toc[fid]) > len(t) + 5:
            return toc[fid]
    return t if not junk else toc.get(fid, t)


@dataclass
class FigureHit:
    pdf_page: int  # 1-based
    chapter: int
    chapter_name: str
    figure_id: str  # e.g. 10-10
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
    # Figure-only page: prefer figure chapter number
    fm = FIG_RE.search(text[:600])
    if fm:
        return int(fm.group("ch")), prev_name or CHAPTER_SLUG.get(int(fm.group("ch")), "unknown")
    return prev_ch, prev_name


def extract_figures(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in FIG_RE.finditer(text):
        fid = f"{m.group('ch')}-{m.group('n')}"
        title = (m.group("title") or "").strip(" .\t–-")
        # drop dotted leaders from TOC lines
        if re.search(r"\.{4,}", title) or re.search(r"\d+\s*[\u2013\-]\s*\d+\s*$", title):
            # TOC style: "Pedestal Assembly........10–35" → take left part
            title = re.split(r"\.{2,}|\s+\d+\s*[\u2013\-]", title)[0].strip(" .\t")
        out.append((fid, title))
    # dedupe keep first
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for fid, title in out:
        if fid in seen:
            continue
        seen.add(fid)
        uniq.append((fid, title))
    return uniq


def is_large_graphic_page(text: str, figures: list[tuple[str, str]]) -> tuple[bool, str]:
    """Heuristic: sparse text and/or figure-dominated page."""
    body = text.strip()
    n = len(body)
    low = body.lower()
    if "intentionally left blank" in low:
        return False, ""
    # Chapter figure TOC pages (many dotted leaders)
    if len(TOC_LINE_RE.findall(body)) >= 3:
        return False, ""
    if body.count("....") >= 5:
        return False, ""
    # pure / near-pure drawing pages
    if n <= 120:
        return True, "sparse_text<=120"
    if n <= 400 and figures:
        return True, "sparse_text<=400+figure"
    if n <= 700 and figures and n / max(body.count("\n") + 1, 1) < 80:
        # short lines, mostly caption
        return True, "caption_heavy<=700"
    # schematic/assembly pages sometimes add a tiny legend
    if figures and n <= 1200:
        # if >50% of non-empty lines look like figure captions / headers / page nums
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if not lines:
            return True, "empty"
        captionish = 0
        for ln in lines:
            if FIG_RE.match(ln) or re.match(r"^\d+\s*[\u2013\-]\s*\d+", ln):
                captionish += 1
            elif re.search(r"844 Series", ln):
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
    """Sanitize manual / markdown basename for a single path segment."""
    s = Path(name).stem if name.lower().endswith((".pdf", ".md")) else name
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.UNICODE).strip("._")
    return s[:120] or "manual"


def resolve_manual_id(doc_id: str | None, toc_md: Path | None, pdf_path: Path) -> str:
    """figures/<manual_id>/<chapter>/… — prefer explicit id, else MD stem, else PDF stem."""
    if doc_id:
        return slug_manual(doc_id)
    if toc_md and toc_md.name:
        # Prefer converted markdown filename (stable short name in knowledge/)
        return slug_manual(toc_md.name)
    return slug_manual(pdf_path.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=ROOT)
    ap.add_argument("--scale", type=float, default=1.5, help="render scale (~108 dpi * scale)")
    ap.add_argument("--limit", type=int, default=0, help="debug: only first N pages")
    ap.add_argument("--dry-run", action="store_true", help="detect only, no render")
    ap.add_argument(
        "--toc-md",
        type=Path,
        default=ROOT / "markdown" / "CS844_Instruction_Manual_v3.3_2024-06.md",
        help="Markdown with List of Figures for title enrichment",
    )
    ap.add_argument(
        "--doc-id",
        type=str,
        default="",
        help="Manual folder name under figures/ (default: toc-md stem, else PDF stem)",
    )
    ap.add_argument(
        "--catalog-only",
        action="store_true",
        help="rebuild catalogs from existing PNGs / meta (re-scan PDF, skip render)",
    )
    args = ap.parse_args()

    pdf_path = args.pdf
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    manual_id = resolve_manual_id(args.doc_id or None, args.toc_md, pdf_path)
    # figures/<manual_id>/<chapter_slug>/fig_….png
    fig_root = args.out / "figures" / manual_id
    cat_root = args.out / "catalogs"
    fig_root.mkdir(parents=True, exist_ok=True)
    cat_root.mkdir(parents=True, exist_ok=True)

    toc = load_toc_titles(args.toc_md)
    print(f"TOC titles loaded: {len(toc)}")
    print(f"figures root: figures/{manual_id}/")

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

        # primary figure id
        if figures:
            fid, title = figures[0]
            title = clean_title(title, fid, toc)
            extras = []
            for a, b in figures[1:]:
                bt = clean_title(b, a, toc)
                extras.append(f"{a}" + (f" {bt}" if bt else ""))
        else:
            # anonymous large graphic page
            fid = f"p{i+1}"
            title = (name or "Graphic") + f" (PDF p.{i+1})"
            extras = []
            # try keep chapter from footer
            if not ch:
                ch, name = prev_ch, prev_name

        ch = ch or prev_ch or 0
        name = name or prev_name
        slug = slug_chapter(ch, name)
        if figures and figures[0][0].count("-") == 1:
            fname = f"fig_{fid.replace('-', '_')}.png"
        else:
            fname = f"page_{i+1:04d}.png"
        # avoid overwrite when multiple pages share figure id (multi-sheet)
        dest_dir = fig_root / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fname
        # Prefer existing multi-page filename if already rendered
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

    # write per-chapter catalogs
    by_ch: dict[int, list[FigureHit]] = defaultdict(list)
    for h in hits:
        by_ch[h.chapter].append(h)

    index_lines = [
        "# CS844 手册 — 大图图录总索引",
        "",
        f"源 PDF：`{pdf_path.name}`",
        f"图片目录：`figures/{manual_id}/`",
        f"检出大图页：**{len(hits)}** / 扫描 {limit}",
        "",
        "| 章 | 名称 | 大图页数 | 图录 |",
        "|----|------|----------|------|",
    ]

    for ch in sorted(by_ch.keys()):
        rows = by_ch[ch]
        name = rows[0].chapter_name
        slug = slug_chapter(ch, name)
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
            img = f"[`{Path(h.image_rel).name}`](../{h.image_rel})" if h.image_rel else ""
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
        print(f"catalog ch{ch:02d}: {len(rows)} -> {cat_path.name}")

    index_lines.extend(["", "## 使用说明", "",
        "- 装配/线路/大插图以 PNG 为准；正文说明仍见 `markdown/CS844_Instruction_Manual_v3.3_2024-06.md`。",
        "- BaoClaw：先查本章图录图题，再打开对应图片（必要时视觉读图）。",
        ""])
    index_path = cat_root / "INDEX.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    meta_path = cat_root / "figures_meta.json"
    meta_path.write_text(
        json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {index_path}")
    print(f"Wrote {meta_path}")
    print(f"Done: {len(hits)} graphic pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
