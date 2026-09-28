#!/usr/bin/env python3
"""把指定页渲染成 PNG，供人工核对判栏/阅读顺序。

用法：
    python3 render_check.py --book guxiejing --pages 53,68,192,406
输出到 results/质检_B/渲染对照/<doc_id>_pXXXX.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
E1_DIR = HERE.parent
sys.path.insert(0, str(E1_DIR))
sys.path.insert(0, str(HERE))

from books import BOOKS, pdf_path  # noqa: E402
from ocr_engine import open_pdf, render_page, save_png  # noqa: E402

OUT = HERE / "results" / "质检_B" / "渲染对照"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    ap.add_argument("--pages", required=True, help="逗号分隔的 1 起页号")
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    doc = open_pdf(pdf_path(args.book))
    doc_id = BOOKS[args.book]["doc_id"]
    for token in args.pages.split(","):
        pg = int(token)
        img, w, h = render_page(doc, pg, scale=args.scale)
        out = OUT / f"{doc_id}_p{pg:04d}.png"
        if not save_png(img, out):
            print(f"失败 {out}")
            continue
        print(f"写出 {out}  ({w}x{h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())