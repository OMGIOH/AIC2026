#!/usr/bin/env python3
"""三册文献全量识别入口：断点续跑、双产物、按册选跑。

用法（在本目录下）：
    python3 run_full.py --list                      # 查看三册页数与进度
    python3 run_full.py --book guge                 # 只跑古歌（989 页）
    python3 run_full.py --book guxiejing            # 只跑古谢经（430 页）
    python3 run_full.py --book daguan               # 只跑文化大观（524 页）
    python3 run_full.py --book all                   # 顺序跑完三册
    python3 run_full.py --book guge --pages 10-30   # 只跑指定范围
    python3 run_full.py --book guge --assemble-only # 不识别，仅重新合成 txt/jsonl

模式：
    guge     → layout（版式还原，与 Swift 版同页验证 100% 一致）
    guxiejing/daguan → plain（每识别块自成一行，阅读顺序，无版式处理）

断点续跑：
    每页独立落盘 lines/pNNNN.jsonl（先写临时文件再改名，页级原子性），
    重跑时自动跳过已完成的页；中断后直接重跑同一命令即可续。

产物（out/<doc_id>/ 下）：
    lines/pNNNN.jsonl        每页记录（page 记录 + line 记录，带几何坐标与置信度）
    <书名>_full_zh.txt       全本纯文本，E0 索引器可直接消费
    <doc_id>_full_lines.jsonl 全本行级结构化数据（全部页合并）
    progress.json            进度（已完成页集合）
"""
from __future__ import annotations

import argparse
import gc
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
E1_DIR = HERE.parent
sys.path.insert(0, str(E1_DIR))          # 复用已验证的引擎与版式模块
sys.path.insert(0, str(HERE))

import layout  # noqa: E402  E1_语料层/layout.py
from ocr_engine import open_pdf, page_count, recognize, render_page  # noqa: E402
from books import BOOKS, ENGINE, pdf_path  # noqa: E402
from plain_mode import detect_columns, restore_plain  # noqa: E402
from verse_mode import build_verses  # noqa: E402

OUT_ROOT = HERE / "out"


def page_file(doc_dir: Path, page: int) -> Path:
    return doc_dir / "lines" / f"p{page:04d}.jsonl"


def load_progress(doc_dir: Path) -> set[int]:
    f = doc_dir / "progress.json"
    if not f.exists():
        return set()
    return {int(x) for x in json.loads(f.read_text(encoding="utf-8"))["done_pages"]}


def save_progress(doc_dir: Path, done: set[int]) -> None:
    f = doc_dir / "progress.json"
    f.write_text(json.dumps({"done_pages": sorted(done)}, ensure_ascii=False),
                 encoding="utf-8")


def process_page(doc, page: int, book: dict, scale: float,
                render_pages: set[int]) -> dict:
    """识别单页并返回 (页记录, 行记录列表, verse 记录列表)。"""
    img, w, h = render_page(doc, page, scale)
    raw_blocks = recognize(img, w, h)

    verses: list[dict] = []
    col_split = None
    if book["mode"] == "layout":
        lines, metas, n_raw, n_kept = layout.restore_page(raw_blocks)
        verses = build_verses(book, page, lines, metas)
    else:
        allow_fb = book.get("col_fallback", False)
        lines, metas, n_raw, n_kept = restore_plain(raw_blocks, w, h, allow_fb)
        cols = detect_columns(raw_blocks, w, h, allow_fb)
        if cols:
            col_split = {"n_cols": 2, "gutter_px": [round(cols[0]), round(cols[1])]}

    page_rec = {
        "type": "page", "doc_id": book["doc_id"], "page": page,
        "mode": book["mode"],
        "render_w": w, "render_h": h,
        "n_blocks_raw": n_raw, "n_blocks_kept": n_kept,
        "n_lines": len(lines),
        "n_verses": len(verses),
        # plain 模式：双栏页记下栏线位置，供行序审计（None = 单栏/未检出）
        "col_split": col_split,
        # layout 模式保留全部原始块（含拼音/音标等被过滤内容），供后续音标层重建
        "raw_blocks": raw_blocks if book["mode"] == "layout" else None,
        "render_image": f"renders/p{page}.png" if page in render_pages else None,
        "canon_version": book["canon_version"], "engine": ENGINE,
    }
    line_recs = []
    for i, (text, meta) in enumerate(zip(lines, metas), 1):
        line_recs.append({
            "type": "line", "doc_id": book["doc_id"], "page": page, "line_no": i,
            "text": text,
            "bbox_px": meta["bbox_px"],
            "n_blocks": meta["n_blocks"], "min_conf": meta["min_conf"],
            "blocks": meta["blocks"],
            "canon_version": book["canon_version"], "engine": ENGINE,
        })

    if page in render_pages:
        from ocr_engine import save_png
        rdir = OUT_ROOT / book["doc_id"] / "renders"
        rdir.mkdir(parents=True, exist_ok=True)
        save_png(img, rdir / f"p{page}.png")

    return {"page_rec": page_rec, "line_recs": line_recs, "verse_recs": verses}


def run_book(key: str, pages: tuple[int, int] | None,
             scale: float, render_pages: set[int]) -> None:
    book = BOOKS[key]
    doc_dir = OUT_ROOT / book["doc_id"]
    (doc_dir / "lines").mkdir(parents=True, exist_ok=True)

    pdf = pdf_path(key)
    doc = open_pdf(pdf)
    total = page_count(doc)
    # 优先级：CLI --pages > 册配置 page_range > 全本
    rng = book.get("page_range")
    start = pages[0] if pages else (rng[0] if rng else 1)
    end = pages[1] if pages else (rng[1] if rng else total)
    end = min(end, total)

    done = load_progress(doc_dir)
    todo = [p for p in range(max(1, start), end + 1) if p not in done]
    print(f"[{key}] {book['title']}（{book['mode']} 模式）"
          f" 共 {total} 页，本批范围 {start}-{end}，待识别 {len(todo)} 页")

    t0 = time.time()
    n_done = 0
    n_verses_total = 0
    for p in todo:
        out = process_page(doc, p, book, scale, render_pages)
        # 页级原子落盘：先写临时文件再改名
        tmp = page_file(doc_dir, p).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(out["page_rec"], ensure_ascii=False) + "\n")
            for r in out["line_recs"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            for r in out["verse_recs"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.rename(page_file(doc_dir, p))
        done.add(p)
        n_verses_total += out["page_rec"]["n_verses"]
        if len(done) % 5 == 0 or not todo:
            save_progress(doc_dir, done)
        n_done += 1
        if n_done % 10 == 0 or n_done == len(todo):
            speed = n_done / max(1e-9, time.time() - t0)
            eta = (len(todo) - n_done) / max(1e-9, speed)
            extra = f"，累计 verse {n_verses_total}" if book["mode"] == "layout" else ""
            print(f"  第 {p} 页完成（{n_done}/{len(todo)}），"
                  f"{speed:.1f} 页/秒，剩余约 {eta/60:.0f} 分钟{extra}")
        if p % 50 == 0:
            gc.collect()

    save_progress(doc_dir, done)
    assemble(key)


def assemble(key: str) -> None:
    """从 lines/pNNNN.jsonl 合成全本 txt 与合并 jsonl（幂等，可随时重跑）。

    layout 模式额外产出：
      <书名>_verse_zh.txt    音译/直译/意译 三层对照视图
      <书名>_意译_zh.txt     纯译文视图（E2 抽取主文本）
      <doc_id>_verses.jsonl  verse 结构化记录
    """
    book = BOOKS[key]
    doc_dir = OUT_ROOT / book["doc_id"]
    files = sorted((doc_dir / "lines").glob("p*.jsonl"))
    if not files:
        print(f"[{key}] 尚无已识别页")
        return

    is_layout = book["mode"] == "layout"
    txt_path = doc_dir / f"{book['title']}_full_zh.txt"
    jsonl_path = doc_dir / f"{book['doc_id']}_full_lines.jsonl"
    verse_txt_path = doc_dir / f"{book['title']}_verse_zh.txt"
    yiyi_txt_path = doc_dir / f"{book['title']}_意译_zh.txt"
    verses_jsonl_path = doc_dir / f"{book['doc_id']}_verses.jsonl"

    n_pages = n_lines = n_verses = 0
    with open(txt_path, "w", encoding="utf-8") as ft, \
            open(jsonl_path, "w", encoding="utf-8") as fj, \
            open(verse_txt_path, "w", encoding="utf-8") as fv, \
            open(yiyi_txt_path, "w", encoding="utf-8") as fy, \
            open(verses_jsonl_path, "w", encoding="utf-8") as fx:
        for f in files:
            page = None
            lines_text: list[str] = []
            verses: list[dict] = []
            for raw in f.read_text(encoding="utf-8").splitlines():
                rec = json.loads(raw)
                fj.write(raw + "\n")
                if rec["type"] == "page":
                    page = rec["page"]
                    n_pages += 1
                elif rec["type"] == "line":
                    lines_text.append(rec["text"])
                    n_lines += 1
                elif rec["type"] == "verse":
                    verses.append(rec)
                    n_verses += 1
            if page is not None:
                ft.write(f"===== 第{page}页 =====\n")
                for t in lines_text:
                    ft.write(t + "\n")
                if is_layout:
                    fv.write(f"===== 第{page}页 =====\n")
                    fy.write(f"===== 第{page}页 =====\n")
                    if verses:
                        for v in verses:
                            if v.get("yin"):
                                fv.write(f"音译：{v['yin']['text']}\n")
                            fv.write(f"直译：{v['zhi']['text'] if v.get('zhi') else ''}\n")
                            fv.write(f"意译：{v['yi']['text'] if v.get('yi') else ''}\n")
                            if v.get("annotation"):
                                fv.write(f"校注：{v['annotation']['text']}\n")
                            if v.get("yi"):
                                fy.write(v["yi"]["text"] + "\n")
                            for flag in v.get("flags", []):
                                fv.write(f"（注：{flag}）\n")
                            fx.write(json.dumps(v, ensure_ascii=False) + "\n")
                    else:
                        # 无 verse 的页也留页标记并注明原因，避免读者误判漏页
                        note = ("（本页未识别出四行对照歌句：散文说明或选译版式，"
                                "全文见 full_zh.txt）" if lines_text
                                else "（空白或图像页）")
                        fv.write(note + "\n")
    print(f"[{key}] 合成完成：{n_pages} 页 / {n_lines} 行"
           f"{f' / {n_verses} verse' if is_layout else ''} → "
           f"{txt_path.name}"
           + (f" + {verse_txt_path.name} + {yiyi_txt_path.name} + {verses_jsonl_path.name}"
              if is_layout else f" + {jsonl_path.name}"))
    if not is_layout:
        # plain 模式无 verse 结构，不留空文件
        for p in (verse_txt_path, yiyi_txt_path, verses_jsonl_path):
            p.unlink(missing_ok=True)


def status() -> None:
    for key, book in BOOKS.items():
        doc_dir = OUT_ROOT / book["doc_id"]
        done = load_progress(doc_dir)
        try:
            total = page_count(open_pdf(pdf_path(key)))
        except Exception:  # noqa: BLE001
            total = "?"
        rng = book.get("page_range")
        scope = f"{rng[0]}-{rng[1]}" if rng else f"1-{total}"
        pct = f"{len(done) / total:.0%}" if isinstance(total, int) else "-"
        print(f"{key:10s} {book['title']:14s} 模式={book['mode']:6s} "
              f"范围 {scope} 进度 {len(done)}（{pct}）")


def parse_pages(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    m = re.match(r"^(\d+)-(\d+)$", s.strip())
    if not m:
        raise SystemExit("--pages 格式应为 起始-结束，如 10-30")
    return int(m.group(1)), int(m.group(2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="all",
                    choices=["all", *BOOKS.keys()], help="选册或全部")
    ap.add_argument("--pages", default=None, help="只跑指定范围，如 10-30")
    ap.add_argument("--scale", type=float, default=3.0)
    ap.add_argument("--render-pages", default="",
                    help="逗号分隔页码，保存渲染图（抽样人工复核用）")
    ap.add_argument("--assemble-only", action="store_true",
                    help="不识别，仅重新合成 txt/jsonl")
    ap.add_argument("--list", action="store_true", help="查看三册进度")
    args = ap.parse_args()

    if args.list:
        status()
        return 0

    pages = parse_pages(args.pages)
    render_pages = {int(x) for x in args.render_pages.split(",") if x.strip()}

    keys = list(BOOKS) if args.book == "all" else [args.book]
    for key in keys:
        if args.assemble_only:
            assemble(key)
        else:
            run_book(key, pages, args.scale, render_pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())