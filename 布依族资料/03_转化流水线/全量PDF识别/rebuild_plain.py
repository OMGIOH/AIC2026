#!/usr/bin/env python3
"""离线重排 plain 模式页的行序：不重跑 OCR，直接从已落盘的行记录重排。

用途：plain_mode 的阅读顺序规则迭代时（双栏切分、视觉行聚类…），秒级重排全部页，
      行文本与既有 OCR 输出零漂移（不必为改排序重跑几十分钟识别）。

原理：行记录已带 blocks（含 bbox_px），排序所需的几何信息齐全。
      排序直接调用 plain_mode.order_plain —— 与在线识别**同一份实现**，
      避免两处规则各自演化（此前 Swift/Python 双实现就因此出现过读数漂移）。
      因 order_plain 是块几何的纯函数，本工具天然幂等：重复跑不产生新变化。

安全校验：重排前后行文本的多重集必须完全一致，否则中止（防止误改内容）。

用法（在本目录下）：
    python3 rebuild_plain.py --book guxiejing             # 重排并重新合成
    python3 rebuild_plain.py --book guxiejing --dry-run   # 只统计，不写盘
    python3 rebuild_plain.py --book daguan --dry-run      # 看单栏册的影响面
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from books import BOOKS  # noqa: E402
from plain_mode import detect_columns, order_plain  # noqa: E402
from run_full import OUT_ROOT, assemble  # noqa: E402


def reorder_page(page_rec: dict, line_recs: list[dict],
                 allow_fallback: bool = False) -> tuple[list[dict], dict]:
    """按 order_plain 重排一页的行记录，返回 (新行记录列表, 统计信息)。

    plain 模式下每行恰含一个块，故「块序」即「行序」；用 id 映射回行记录，
    保留行记录上的其余字段（conf、doc_id 等）。
    """
    block_to_line: dict[int, dict] = {}
    all_blocks: list[dict] = []
    for r in line_recs:
        for b in r.get("blocks", []):
            block_to_line[id(b)] = r
            all_blocks.append(b)

    rows = order_plain(all_blocks, page_rec["render_w"], page_rec["render_h"],
                       allow_fallback)
    new: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        for b in row:
            r = block_to_line[id(b)]
            if id(r) not in seen:          # 一行含多块时只保留一次
                seen.add(id(r))
                new.append(r)
    # 理论不可达：若有块未进入结果，补回原序末尾，避免丢行
    if len(new) != len(line_recs):
        for r in line_recs:
            if id(r) not in seen:
                seen.add(id(r))
                new.append(r)

    cols = detect_columns(all_blocks, page_rec["render_w"], page_rec["render_h"],
                          allow_fallback)
    spreads = [max(b["bbox_px"]["y"] for b in row) - min(b["bbox_px"]["y"] for b in row)
               for row in rows if len(row) > 1]
    return new, {
        "n_cols": 2 if cols else 1,
        "gutter_px": [round(cols[0]), round(cols[1])] if cols else None,
        "n_rows": len(rows),
        "n_multi_rows": len(spreads),
        "max_row_spread": max(spreads) if spreads else 0,
        "changed": any(a is not b for a, b in zip(new, line_recs)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    book = BOOKS[args.book]
    if book["mode"] != "plain":
        print(f"[{args.book}] 非 plain 模式（{book['mode']}），无需重排")
        return 0

    doc_dir = OUT_ROOT / book["doc_id"]
    files = sorted((doc_dir / "lines").glob("p*.jsonl"))
    if not files:
        print(f"[{args.book}] 尚无已识别页")
        return 1

    n_pages = n_two = n_changed_pg = 0
    worst: list[tuple[int, int]] = []          # (spread, page) 供人工核对
    allow_fallback = bool(book.get("col_fallback", False))
    for f in files:
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        page_recs = [r for r in recs if r["type"] == "page"]
        if not page_recs:
            continue
        pg = dict(page_recs[0])
        line_recs = [r for r in recs if r["type"] == "line"]
        other = [r for r in recs if r["type"] not in ("page", "line")]

        new_lines, st = reorder_page(pg, line_recs, allow_fallback)

        # 安全校验：文本多重集必须不变
        if Counter(r["text"] for r in new_lines) != Counter(r["text"] for r in line_recs):
            print(f"  第{pg['page']}页 校验失败：重排前后文本不一致，已中止")
            return 2

        n_pages += 1
        if st["n_cols"] == 2:
            n_two += 1
        if st["changed"]:
            n_changed_pg += 1
        if st["max_row_spread"] > 20:
            worst.append((st["max_row_spread"], pg["page"]))

        if args.dry_run:
            continue

        for i, r in enumerate(new_lines, 1):
            r["line_no"] = i
            r["canon_version"] = book["canon_version"]
        pg["n_lines"] = len(new_lines)
        pg["col_split"] = ({"n_cols": 2, "gutter_px": st["gutter_px"]}
                           if st["n_cols"] == 2 else None)
        pg["canon_version"] = book["canon_version"]

        out = [json.dumps(pg, ensure_ascii=False)]
        out += [json.dumps(r, ensure_ascii=False) for r in new_lines]
        out += [json.dumps(r, ensure_ascii=False) for r in other]
        tmp = f.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.rename(f)

    worst.sort(reverse=True)
    print(f"[{args.book}] 重排：{n_pages} 页（双栏 {n_two}，行序变化 {n_changed_pg}）"
          + ("（dry-run，未写盘）" if args.dry_run else f"，canon → {book['canon_version']}"))
    print(f"  视觉行抖动 >20px 的页：{len(worst)} 页"
          + (f"，最大 {worst[0][0]}px (p{worst[0][1]})" if worst else ""))
    if worst:
        print("  待人工核对（前 10）：" + ", ".join(f"p{p}({s}px)" for s, p in worst[:10]))
    if not args.dry_run:
        assemble(args.book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())