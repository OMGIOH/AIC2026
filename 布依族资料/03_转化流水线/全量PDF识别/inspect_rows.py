#!/usr/bin/env python3
"""打印某册某页的块几何（按 order_plain 的阅读顺序），供视觉核对时对照坐标。

用法（在本目录下）：
    python3 inspect_rows.py --book guxiejing --page 363
    python3 inspect_rows.py --book guxiejing --page 363 --y0 850 --y1 1300
输出：判栏结果 + 每个视觉行的 y 区间、块数、抖动，以及行内各块的 x/y/w/h/conf/文本。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from books import BOOKS  # noqa: E402
from plain_mode import detect_columns, order_plain  # noqa: E402
from run_full import OUT_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--y0", type=float)
    ap.add_argument("--y1", type=float)
    ap.add_argument("--max-text", type=int, default=60)
    args = ap.parse_args()

    doc_id = BOOKS[args.book]["doc_id"]
    allow_fb = bool(BOOKS[args.book].get("col_fallback", False))
    f = OUT_ROOT / doc_id / "lines" / f"p{args.page:04d}.jsonl"
    recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    pg = [r for r in recs if r["type"] == "page"][0]
    blocks = [b for r in recs if r["type"] == "line" for b in r["blocks"]]

    cols = detect_columns(blocks, pg["render_w"], pg["render_h"], allow_fb)
    print(f"{doc_id} p{args.page}  {pg['render_w']}x{pg['render_h']}  "
          f"blocks={len(blocks)}  判栏={'双栏 ' + str([round(c) for c in cols]) if cols else '单栏'}")

    rows = order_plain(blocks, pg["render_w"], pg["render_h"], allow_fb)
    for i, row in enumerate(rows):
        ys = [b["bbox_px"]["y"] for b in row]
        y0 = min(ys)
        y1 = max(b["bbox_px"]["y"] + b["bbox_px"]["h"] for b in row)
        if args.y0 is not None and (y1 < args.y0 or y0 > args.y1):
            continue
        tag = f"  抖动={max(ys) - min(ys):.0f}px" if len(row) > 1 else ""
        print(f"[{i:03d}] y{y0:.0f}-{y1:.0f}  n={len(row)}{tag}")
        for b in sorted(row, key=lambda b: b["bbox_px"]["x"]):
            p = b["bbox_px"]
            t = b["text"][:args.max_text]
            print(f"        x{p['x']:5d} y{p['y']:5d} w{p['w']:4d} h{p['h']:3d} c{b['conf']:.2f}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())