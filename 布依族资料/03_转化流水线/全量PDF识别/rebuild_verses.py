#!/usr/bin/env python3
"""离线重算 verse：不重跑 OCR，直接从已落盘的行记录重建歌句。

用途：verse 切分规则迭代时（如新增选译体支持、调阈值），几秒钟重算全部页，
而不必重跑 300 页 OCR（约 4 分钟）且保证行记录与 OCR 输出零漂移。

原理：行记录里已保留 blocks（含 bbox_norm/bbox_px/conf），verse 重建所需的
全部几何信息都在，只有 avg_block_h 需要从块高重算（与 join_band 同口径）。

用法：
    python3 rebuild_verses.py --book guge          # 重算后自动 assemble
    python3 rebuild_verses.py --book guge --dry-run # 只统计，不写盘
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from books import BOOKS, ENGINE  # noqa: E402
from verse_mode import build_verses  # noqa: E402
from run_full import OUT_ROOT, assemble  # noqa: E402


def rebuild_meta(line_rec: dict) -> dict:
    """从行记录重建 build_verses 需要的 meta 结构。"""
    blocks = line_rec["blocks"]
    heights = [b["bbox_norm"]["h"] for b in blocks if b.get("bbox_norm")]
    return {
        "blocks": blocks,
        "avg_block_h": round(sum(heights) / len(heights), 6) if heights else 0.0,
        "bbox_px": line_rec.get("bbox_px", {}),
        "n_blocks": line_rec.get("n_blocks", len(blocks)),
        "min_conf": line_rec.get("min_conf", 1.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    book = BOOKS[args.book]
    if book["mode"] != "layout":
        print(f"[{args.book}] 非 layout 模式，无 verse 可重建")
        return 0
    doc_dir = OUT_ROOT / book["doc_id"]
    files = sorted((doc_dir / "lines").glob("p*.jsonl"))
    if not files:
        print(f"[{args.book}] 尚无已识别页")
        return 1

    n_pages = n_verses = 0
    for f in files:
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        page_recs = [r for r in recs if r["type"] == "page"]
        line_recs = [r for r in recs if r["type"] == "line"]
        if not page_recs:
            continue
        pg = dict(page_recs[0])
        lines = [r["text"] for r in line_recs]
        metas = [rebuild_meta(r) for r in line_recs]

        book_ctx = {**book, "_engine": ENGINE}
        verses = build_verses(book_ctx, pg["page"], lines, metas)
        n_pages += 1
        n_verses += len(verses)

        if args.dry_run:
            continue
        pg["n_verses"] = len(verses)
        out = [json.dumps(pg, ensure_ascii=False)]
        out += [json.dumps(r, ensure_ascii=False) for r in line_recs]
        out += [json.dumps(v, ensure_ascii=False) for v in verses]
        tmp = f.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.rename(f)

    print(f"[{args.book}] verse 重算：{n_pages} 页 / {n_verses} 个"
          + ("（dry-run，未写盘）" if args.dry_run else ""))
    if not args.dry_run:
        assemble(args.book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())