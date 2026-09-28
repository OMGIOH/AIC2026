#!/usr/bin/env python3
"""把若干张图拼成一张对照大图（contact sheet），供视觉模型一次读多张。

用途：判栏/行序核验需要逐张看图，但逐张读图成本高。本工具把 N 张按网格拼一张，
      每格下方标页号，一次读图即可判多张（适合判「双栏 / 单栏 / 表格」这类粗判）。

用法（在本目录下）：
    python3 contact_sheet.py --book guxiejing --pages 17,18,25 --out 古谢经兜底页.png
    # 也可直接拼裁条：--glob 相对 results/质检_B 的通配符
    python3 contact_sheet.py --glob '裁条对照/古谢经_*.png' --cols 1 --out 古谢经裁条.png
"""
from __future__ import annotations

import argparse
import glob as globlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from books import BOOKS  # noqa: E402

QC_DIR = HERE / "results" / "质检_B"
RENDER_DIR = QC_DIR / "渲染对照"
CELL_W = 300
COLS = 6
PAD = 6
LABEL_H = 18


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=list(BOOKS))
    ap.add_argument("--pages", help="逗号分隔的 1 起页号")
    ap.add_argument("--glob", help="相对 results/质检_B 的通配符，如 '裁条对照/古谢经_*.png'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--cell", type=int, default=CELL_W)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.glob:
        files = sorted(globlib.glob(str(QC_DIR / args.glob)))
        if args.skip:
            files = files[args.skip:]
        if args.limit:
            files = files[:args.limit]
        tiles = [(Path(f).stem, Image.open(f).convert("RGB")) for f in files]
    elif args.book and args.pages:
        doc_id = BOOKS[args.book]["doc_id"]
        tiles = []
        for pg in (int(t) for t in args.pages.split(",")):
            f = RENDER_DIR / f"{doc_id}_p{pg:04d}.png"
            if not f.exists():
                print(f"缺渲染图：{f}")
                continue
            tiles.append((f"p{pg}", Image.open(f).convert("RGB")))
    else:
        print("需给出 --glob，或 --book 与 --pages")
        return 1

    resized = []
    for label, im in tiles:
        h = int(im.height * args.cell / im.width)
        resized.append((label, im.resize((args.cell, h), Image.LANCZOS)))
    if not resized:
        print("无可用图")
        return 1

    cell_h = max(t.height for _, t in resized)
    rows = (len(resized) + args.cols - 1) // args.cols
    W = args.cols * (args.cell + PAD) + PAD
    H = rows * (cell_h + LABEL_H + PAD) + PAD
    sheet = Image.new("RGB", (W, H), (235, 235, 235))
    d = ImageDraw.Draw(sheet)
    for i, (label, t) in enumerate(resized):
        r, c = divmod(i, args.cols)
        x = PAD + c * (args.cell + PAD)
        y = PAD + r * (cell_h + LABEL_H + PAD)
        sheet.paste(t, (x, y))
        d.text((x + 4, y + cell_h + 2), label[-28:], fill=(0, 0, 0))
    out = QC_DIR / args.out
    sheet.save(out)
    print(f"写出 {out}  ({W}x{H}, {len(resized)} 张)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())