#!/usr/bin/env python3
"""把「视觉行待核」清单里的每条目，按 y 区间从原页裁出高清条带，供视觉判读。

用途：人工（或视觉模型）核对「同一视觉行内的块到底该不该并成一行」时，
      不必打开整页 PDF 逐页找位置——本工具直接按清单给出的 y 起止，
      从 3 倍渲染图（与 bbox_px 同一像素空间）裁出条带并放大，逐条对照。

坐标约定：清单里的 y 起 / y 止是 ocr_engine.recognize 输出的 bbox_px（scale=3.0 像素空间）。
      本工具因此固定用 scale=3.0 渲染，保证 1:1 对齐；PAD 上下各留白便于看邻行。

用法（在本目录下）：
    python3 crop_check.py                      # 全部条目
    python3 crop_check.py --short 文化大观      # 只做某册
    python3 crop_check.py --pages 273,276      # 只做某几页
输出到 results/质检_B/裁条对照/<短名>_pXXXX_<序号>.png
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
E1_DIR = HERE.parent
sys.path.insert(0, str(E1_DIR))
sys.path.insert(0, str(HERE))

import Quartz  # noqa: E402

from books import BOOKS, pdf_path  # noqa: E402
from ocr_engine import open_pdf, render_page, save_png  # noqa: E402

OUT = HERE / "results" / "质检_B" / "裁条对照"
CSV_IN = HERE / "results" / "质检_B" / "视觉行待核.csv"
SCALE = 3.0
PAD = 55.0  # 上下留白（scale=3.0 像素）

SHORT_TO_KEY = {"古歌": "guge", "古谢经": "guxiejing", "文化大观": "daguan"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(CSV_IN))
    ap.add_argument("--short", help="只做某册（短名：古歌/古谢经/文化大观）")
    ap.add_argument("--pages", help="只做某几页，逗号分隔")
    ap.add_argument("--pad", type=float, default=PAD)
    args = ap.parse_args()

    pages_filter = {int(t) for t in args.pages.split(",")} if args.pages else None
    with Path(args.csv).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[str, object] = {}
    made = 0
    for i, r in enumerate(rows, start=1):
        short = r["册"]
        if args.short and short != args.short:
            continue
        pg = int(r["页"])
        if pages_filter and pg not in pages_filter:
            continue
        key = SHORT_TO_KEY[short]
        doc_id = BOOKS[key]["doc_id"]
        if key not in cache:
            cache[key] = open_pdf(pdf_path(key))
        img, w, h = render_page(cache[key], pg, scale=SCALE)
        y0 = max(0.0, float(r["y起"]) - args.pad)
        y1 = min(float(h), float(r["y止"]) + args.pad)
        rect = Quartz.CGRectMake(0.0, y0, float(w), y1 - y0)
        band = Quartz.CGImageCreateWithImageInRect(img, rect)
        if band is None:
            print(f"裁切失败 {short} p{pg}")
            continue
        out = OUT / f"{short}_{doc_id}_p{pg:04d}_{i:02d}.png"
        if not save_png(band, out):
            print(f"写盘失败 {out}")
            continue
        made += 1
        print(f"{out.name}  抖动{r['抖动px']}px  {r['行内容（按x序）'][:60]}")
    print(f"共裁出 {made} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())