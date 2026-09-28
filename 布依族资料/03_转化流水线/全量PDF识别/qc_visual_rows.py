#!/usr/bin/env python3
"""重新生成「视觉行待核」清单：列出同一视觉行内块间垂直抖动 >20px 的行。

用途：视觉行聚类（plain_mode.cluster_rows）的判据一有改动，就重跑本脚本刷新清单，
      供人工核对「这些块到底该不该并成一行」。

判据回顾：聚类只决定**排序键**，不合并文本。抖动大说明该行内各块的 y 估计差异大，
可能是（a）Vision 抖动、（b）本不该合并的两行被链式并进来、
（c）跨栏/表格结构的相邻单元被并成一行——故需人工判「应合并 / 应拆开」。

用法（在本目录下）：
    python3 qc_visual_rows.py                 # 全部 plain 模式册
    python3 qc_visual_rows.py --book guxiejing
输出：results/质检_B/视觉行待核.csv（utf-8-sig，Excel 可直接打开）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from books import BOOKS  # noqa: E402
from plain_mode import order_plain  # noqa: E402
from run_full import OUT_ROOT  # noqa: E402

OUT_CSV = HERE / "results" / "质检_B" / "视觉行待核.csv"
SPREAD_PX = 20.0
SHORT_NAME = {"buyi_epic_12204413": "古歌", "buyi_guxiejing": "古谢经", "buyi_daguan": "文化大观"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=list(BOOKS), help="只处理某一册，缺省为全部 plain 模式册")
    args = ap.parse_args()

    books = [args.book] if args.book else [k for k, v in BOOKS.items() if v["mode"] == "plain"]
    rows: list[list] = []
    for key in books:
        book = BOOKS[key]
        short = SHORT_NAME.get(book["doc_id"], key)
        for f in sorted((OUT_ROOT / book["doc_id"] / "lines").glob("p*.jsonl")):
            recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
            page_recs = [r for r in recs if r["type"] == "page"]
            if not page_recs:
                continue
            pg = page_recs[0]
            blocks = [b for r in recs if r["type"] == "line" for b in r["blocks"]]
            if not blocks:
                continue
            for row in order_plain(blocks, pg["render_w"], pg["render_h"],
                                   bool(book.get("col_fallback", False))):
                if len(row) < 2:
                    continue
                ys = [b["bbox_px"]["y"] for b in row]
                spread = max(ys) - min(ys)
                if spread <= SPREAD_PX:
                    continue
                row = sorted(row, key=lambda b: b["bbox_px"]["x"])
                rows.append([
                    short, pg["page"], round(spread), len(row),
                    round(min(ys)), round(max(ys) + max(b["bbox_px"]["h"] for b in row)),
                    " | ".join(b["text"][:24] for b in row),
                    "", "",
                ])

    rows.sort(key=lambda r: -r[2])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["册", "页", "抖动px", "块数", "y起", "y止", "行内容（按x序）",
                    "判定(应合并/应拆开)", "备注"])
        w.writerows(rows)
    print(f"写出 {OUT_CSV}：{len(rows)} 条（抖动 >{SPREAD_PX:.0f}px 的视觉行）")
    for key in books:
        short = SHORT_NAME.get(BOOKS[key]["doc_id"], key)
        print(f"  {short}: {sum(1 for r in rows if r[0] == short)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())