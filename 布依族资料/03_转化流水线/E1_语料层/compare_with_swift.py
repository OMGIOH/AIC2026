#!/usr/bin/env python3
"""Python 版 vs Swift 版 OCR 同页对照。

两版调用同一个 Vision 引擎、同一套版式参数，理论上应逐字相同；
对照的意义是把「移植正确性」变成可审计的数字，而不是靠肉眼扫一遍。

用法（E1_语料层 目录下）：
    python3 compare_with_swift.py \
        --swift ../../布依族资源/PDF识别/布依族古歌_12204413_p10-30_zh.txt \
        --py    out/布依族古歌_12204413_p10-30_zh.txt

输出：results/compare_report.md + 控制台摘要
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

PAGE_MARK = re.compile(r"^=+\s*第\s*(\d+)\s*页\s*=+\s*$")


def parse_pages(path: Path) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    cur: int | None = None
    for raw in open(path, "r", encoding="utf-8-sig"):
        line = raw.rstrip("\n")
        m = PAGE_MARK.match(line)
        if m:
            cur = int(m.group(1))
            pages.setdefault(cur, [])
            continue
        if cur is None:
            if line.strip():
                cur = 1
                pages.setdefault(1, [])
            else:
                continue
        if line.strip():
            pages[cur].append(line)
    return pages


def norm(line: str) -> str:
    """规范化比较：去所有空白（含全角空格 U+3000）。"""
    return re.sub(r"\s+", "", line)


def compare(swift_pages: dict[int, list[str]], py_pages: dict[int, list[str]]):
    rows = []
    tot_s = tot_p = same_exact = same_norm = 0
    mismatches: list[tuple[int, str, str]] = []   # (page, which, line)

    for page in sorted(set(swift_pages) | set(py_pages)):
        s_lines = swift_pages.get(page, [])
        p_lines = py_pages.get(page, [])
        cs, cp = Counter(s_lines), Counter(p_lines)
        exact = sum((cs & cp).values())
        ns = Counter(norm(x) for x in s_lines)
        np_ = Counter(norm(x) for x in p_lines)
        n_same = sum((ns & np_).values())

        tot_s += len(s_lines)
        tot_p += len(p_lines)
        same_exact += exact
        same_norm += n_same

        if page not in swift_pages or page not in py_pages or len(s_lines) != len(p_lines) or exact < max(len(s_lines), len(p_lines)):
            # 收集差异样例（每页最多 4 条）
            only_s = list((cs - cp).elements())[:2]
            only_p = list((cp - cs).elements())[:2]
            for x in only_s:
                mismatches.append((page, "swift独有", x))
            for x in only_p:
                mismatches.append((page, "python独有", x))

        rows.append({
            "page": page,
            "swift": len(s_lines), "py": len(p_lines),
            "exact": exact, "norm_same": n_same,
        })
    return rows, tot_s, tot_p, same_exact, same_norm, mismatches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swift", required=True)
    ap.add_argument("--py", required=True)
    ap.add_argument("--out", default="results/compare_report.md")
    ap.add_argument("--max-examples", type=int, default=12)
    args = ap.parse_args()

    sw = parse_pages(Path(args.swift))
    py = parse_pages(Path(args.py))
    rows, tot_s, tot_p, same_exact, same_norm, mism = compare(sw, py)

    rate_exact = same_exact / max(tot_s, tot_p) if max(tot_s, tot_p) else 0
    rate_norm = same_norm / max(tot_s, tot_p) if max(tot_s, tot_p) else 0

    L = []
    L.append("# E1 同页对照：Python 版 vs Swift 版 OCR")
    L.append("")
    L.append(f"- Swift 文本：`{args.swift}`")
    L.append(f"- Python 文本：`{args.py}`")
    L.append(f"- 页范围：第 {rows[0]['page']} - {rows[-1]['page']} 页，共 {len(rows)} 页" if rows else "- 无页面")
    L.append("")
    L.append("## 总体结果")
    L.append("")
    L.append("| 指标 | Swift 行数 | Python 行数 | 完全相同 | 规范化后相同 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| 数值 | {tot_s} | {tot_p} | {same_exact} | {same_norm} |")
    L.append("")
    L.append(f"**逐字一致率 = {same_exact}/{max(tot_s, tot_p)} = {rate_exact:.4f}；"
             f"规范化（去空白）后一致率 = {same_norm}/{max(tot_s, tot_p)} = {rate_norm:.4f}**")
    L.append("")
    if mism:
        L.append(f"## 差异样例（前 {min(args.max_examples, len(mism))} 条）")
        L.append("")
        L.append("| 页 | 归属 | 行内容 |")
        L.append("|---|---|---|")
        for page, which, text in mism[:args.max_examples]:
            L.append(f"| p{page} | {which} | {text[:50]} |")
    else:
        L.append("## 差异样例")
        L.append("")
        L.append("无。两版输出逐行完全一致。")
    L.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"Swift 行数 {tot_s} / Python 行数 {tot_p}")
    print(f"逐字一致 {same_exact}（{rate_exact:.2%}），规范化后一致 {same_norm}（{rate_norm:.2%}）")
    print(f"差异样例 {len(mism)} 条，报告已写: {out}")
    if rate_exact == 1.0:
        print("结论：Python 版与 Swift 版同页输出完全一致，移植正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())