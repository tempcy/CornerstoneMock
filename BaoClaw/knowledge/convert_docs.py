#!/usr/bin/env python3
"""Batch-convert knowledge/raw/** into knowledge/markdown/** via MarkItDown.

Preserves relative paths under raw/ (e.g. raw/manuals/foo.pdf → markdown/manuals/foo.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

from markitdown import MarkItDown

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "markdown"
INCLUDE = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".xml",
    ".png",
    ".jpg",
    ".jpeg",
}


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    md = MarkItDown()
    files = [p for p in RAW.rglob("*") if p.is_file() and p.suffix.lower() in INCLUDE]
    if not files:
        print(f"No convertible files under {RAW}")
        print("Drop files into knowledge/raw/<category>/ then re-run.")
        return 0
    ok = 0
    for src in sorted(files):
        rel = src.relative_to(RAW)
        dest = OUT / rel.with_suffix(".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = md.convert(str(src))
            text = (result.text_content or "").strip()
            header = f"<!-- source: {rel.as_posix()} -->\n\n"
            dest.write_text(header + text + "\n", encoding="utf-8")
            print(f"OK  {rel.as_posix()} -> {dest.relative_to(ROOT).as_posix()}")
            ok += 1
        except Exception as e:
            print(f"FAIL {rel}: {e}", file=sys.stderr)
    print(f"Done: {ok}/{len(files)}")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
