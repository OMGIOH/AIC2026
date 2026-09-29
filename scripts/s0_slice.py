#!/usr/bin/env python3
"""S0 切片与版本语料集 —— 《安王与祖王》(古歌本 p904-985)

规则代码，仅用 Python 标准库。语料输入为本仓库快照 `布依族资源/`（canon 与源项目一致）。

产出（相对仓库根）：
  pieces/awzw/awzw_yi_verses.jsonl                    意译层切片（verse 级，十四节拍标注+指针）
  pieces/awzw/awzw_full_lines.jsonl                   full 层切片（行级，同页范围，含散文导读）
  pieces/awzw/qa/tail_p983-989.txt                    篇尾乱码区逐行查验件（人工核对入口）
  pieces/awzw/versions/daguan_mubodong_p121-122.jsonl 大观《穆播董》整页切片
  pieces/awzw/versions/daguan_mubodong_p121-122.txt   同上可读版
  pieces/awzw/versions/daguan_hit_pages.csv           大观全本 安王/祖王/盘果 逐页命中
  pieces/awzw/piece_meta.json
  pieces/awzw/s0_report.md
"""

import csv
import json
import random
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "布依族资源"
OUT = ROOT / "pieces" / "awzw"

PIECE = "安王与祖王"
PIECE_ID = "awzw"
YI_RANGE = (904, 985)      # 意译层切片范围（篇目范围，正文首见 p910）
FULL_RANGE = (904, 985)    # full 层同页范围（对齐补散文导读）
TAIL_QA_RANGE = (983, 989) # 篇尾元数据/乱码区查验范围

# 十四节拍表（设计方案 §2.1，页级近似；边界页可属两拍）
BEATS = [
    (1,  "盘果河畔赞鱼；鱼女夜来吹笛弹弦，成婚",              904, 909),
    (2,  "安王出生；13岁捕鱼欲烹“外祖父外祖母”，鱼母遁水，成孤儿", 910, 913),
    (3,  "两商人两度说媒，盘果娶寡妇",                        915, 933),
    (4,  "兄弟河边抢鱼争地，结冤",                            933, 934),
    (5,  "后母送饭不均（艾枝下/枸树上），祖王告状",            935, 946),
    (6,  "后母唆祖王“杀哥夺大印”",                            946, 948),
    (7,  "兄弟斗法：互施鸟灾/黑暗长夜/痢疾天花 vs 织网/火炬/雷火", 948, 961),
    (8,  "安王召兵，父气病",                                  962, 962),
    (9,  "祖王遣鹰、鸦使请兄归",                              963, 969),
    (10, "父索“巧简井、苏者鲶”水；兄弟掘井，祖王填土害兄",     969, 977),
    (11, "鱼母雷中救子，抛出洞外",                            977, 978),
    (12, "兄弟相打，安王飞上天",                              978, 979),
    (13, "安王降鸟灾、黑夜、瘟疫；祖王遍寻大哥",               980, 982),
    (14, "老人公证分河分地；安王不受，令年年纳贡",             982, 985),
]

# 篇尾元数据：2026-09-29 人工从 full 层 p985 读出（乱码行之下，qa 文件可复核）
TAIL_METADATA = {
    "流传地区": "罗甸、望谟、册享一带",
    "唱述人": "望谟县城关镇上院廖家园",
    "搜集整理": "黄义仁",
    "搜集时间": "1962年4月",
    "source_page": 985,
    "status": "已从 full 层 p985 读出，待人工复核 qa/tail_p983-989.txt",
}

# 大观命中核验基准（设计方案 §1.1 所记）
DAGUAN_EXPECTED = {"安王": 137, "祖王": 112, "盘果": 28}
DAGUAN_KEYWORDS = ["安王", "祖王", "盘果"]
DAGUAN_MUBODONG_PAGES = (121, 122)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def beats_for_page(page):
    nos = [no for no, _name, lo, hi in BEATS if lo <= page <= hi]
    return nos, len(nos) > 1


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def slice_yi_verses():
    """意译层切片：verse 原记录 + beats + 证据指针五要素（指针指向 yi 层所在规范行）。"""
    out = []
    for r in load_jsonl(CORPUS / "buyi_epic_12204413_verses.jsonl"):
        if r.get("type") != "verse" or not (YI_RANGE[0] <= r["page"] <= YI_RANGE[1]):
            continue
        yi = r.get("yi") or {}
        beats, boundary = beats_for_page(r["page"])
        rec = dict(r)
        rec["piece"] = PIECE
        rec["beats"] = beats
        rec["beat_boundary"] = boundary
        rec["pointer"] = {
            "doc_id": r["doc_id"],
            "page": r["page"],
            "line_no": yi.get("line_no"),
            "char_start": yi.get("char_start"),
            "char_end": yi.get("char_end"),
            "canon_version": r.get("canon_version"),
        }
        out.append(rec)
    write_jsonl(OUT / "awzw_yi_verses.jsonl", out)
    return out


def slice_full_lines():
    """full 层切片：行级原记录 + beats + piece。"""
    out = []
    for r in load_jsonl(CORPUS / "buyi_epic_12204413_full_lines.jsonl"):
        if r.get("type") != "line" or not (FULL_RANGE[0] <= r["page"] <= FULL_RANGE[1]):
            continue
        beats, boundary = beats_for_page(r["page"])
        rec = dict(r)
        rec["piece"] = PIECE
        rec["beats"] = beats
        rec["beat_boundary"] = boundary
        out.append(rec)
    write_jsonl(OUT / "awzw_full_lines.jsonl", out)
    return out


def qa_tail():
    """篇尾乱码区逐行查验件。疑似乱码标记：含拉丁字母 或 min_conf<0.6。"""
    flagged = []
    with open(OUT / "qa" / "tail_p983-989.txt", "w", encoding="utf-8") as f:
        f.write("# 《安王与祖王》篇尾查验件 p983-989（full 层逐行，含置信度）\n")
        f.write("# 疑似乱码判据：文本含拉丁字母 或 min_conf<0.6（自动标记，人工复核为准）\n")
        f.write(f"# 生成日期：{date.today().isoformat()}\n\n")
        for r in load_jsonl(CORPUS / "buyi_epic_12204413_full_lines.jsonl"):
            if r.get("type") != "line" or not (TAIL_QA_RANGE[0] <= r["page"] <= TAIL_QA_RANGE[1]):
                continue
            text = r.get("text", "")
            conf = r.get("min_conf")
            garbled = bool(re.search(r"[A-Za-z]", text)) or (conf is not None and conf < 0.6)
            mark = "  ??疑似乱码" if garbled else ""
            f.write(f"p{r['page']} L{r['line_no']} conf={conf}: {text}{mark}\n")
            if garbled and TAIL_METADATA["source_page"] - 2 <= r["page"] <= TAIL_METADATA["source_page"] + 3:
                flagged.append({"page": r["page"], "line_no": r["line_no"], "text": text, "min_conf": conf})
    return flagged


def verify_pointers(yi_records):
    """指针回放校验：yi 文本须 == full 层规范行 [char_start:char_end]。"""
    line_map = {}
    for r in load_jsonl(CORPUS / "buyi_epic_12204413_full_lines.jsonl"):
        if r.get("type") == "line":
            line_map[(r["page"], r["line_no"])] = r.get("text", "")
    ok, bad = 0, []
    for rec in yi_records:
        p = rec["pointer"]
        src = line_map.get((p["page"], p["line_no"]))
        if src is None:
            bad.append({**p, "reason": "源行不存在"})
            continue
        cs, ce = p["char_start"], p["char_end"]
        if cs is None or ce is None:
            bad.append({**p, "reason": "char 区间缺失"})
            continue
        if src[cs:ce] == (rec.get("yi") or {}).get("text", ""):
            ok += 1
        else:
            bad.append({**p, "reason": "回放不一致", "expect": (rec.get("yi") or {}).get("text", ""),
                        "got": src[cs:ce]})
    return ok, bad


def daguan_slice():
    """大观《穆播董》P121-122 整页切片（jsonl + 可读 txt）。"""
    base = OUT / "versions"
    recs, txt_lines = [], []
    cur_page = None
    for r in load_jsonl(CORPUS / "buyi_daguan_full_lines.jsonl"):
        if r.get("type") != "line" or r["page"] not in DAGUAN_MUBODONG_PAGES:
            continue
        recs.append(r)
        if r["page"] != cur_page:
            cur_page = r["page"]
            txt_lines.append(f"\n===== 第{cur_page}页 =====\n")
        txt_lines.append(r.get("text", ""))
    write_jsonl(base / "daguan_mubodong_p121-122.jsonl", recs)
    (base / "daguan_mubodong_p121-122.txt").write_text(
        "# 大观《穆播董》（也译《安王与祖王》）P121-122 整页切片｜doc_id=buyi_daguan canon=v1.1-ocr\n"
        + "".join(txt_lines), encoding="utf-8")
    return recs


def daguan_hits():
    """大观全本逐页命中计数 → CSV（utf-8-sig），并汇总核验。"""
    from collections import defaultdict
    per_page = defaultdict(lambda: {k: 0 for k in DAGUAN_KEYWORDS})
    for r in load_jsonl(CORPUS / "buyi_daguan_full_lines.jsonl"):
        if r.get("type") != "line":
            continue
        text = r.get("text", "")
        for k in DAGUAN_KEYWORDS:
            c = text.count(k)
            if c:
                per_page[r["page"]][k] += c
    totals = {k: 0 for k in DAGUAN_KEYWORDS}
    with open(OUT / "versions" / "daguan_hit_pages.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["页码"] + DAGUAN_KEYWORDS + ["合计"])
        for page in sorted(per_page):
            row = per_page[page]
            w.writerow([page] + [row[k] for k in DAGUAN_KEYWORDS] + [sum(row.values())])
            for k in DAGUAN_KEYWORDS:
                totals[k] += row[k]
    diff = {k: {"actual": totals[k], "expected": DAGUAN_EXPECTED[k], "delta": totals[k] - DAGUAN_EXPECTED[k]}
            for k in DAGUAN_KEYWORDS}
    return totals, diff, len(per_page)


def main():
    random.seed(42)
    (OUT / "qa").mkdir(parents=True, exist_ok=True)
    (OUT / "versions").mkdir(parents=True, exist_ok=True)

    yi_records = slice_yi_verses()
    full_records = slice_full_lines()
    flagged = qa_tail()
    ok, bad = verify_pointers(yi_records)
    daguan_recs = daguan_slice()
    totals, diff, hit_pages = daguan_hits()

    # 对账：切片数 == 源范围内记录数
    src_verses = sum(1 for r in load_jsonl(CORPUS / "buyi_epic_12204413_verses.jsonl")
                     if r.get("type") == "verse" and YI_RANGE[0] <= r["page"] <= YI_RANGE[1])
    src_lines = sum(1 for r in load_jsonl(CORPUS / "buyi_epic_12204413_full_lines.jsonl")
                    if r.get("type") == "line" and FULL_RANGE[0] <= r["page"] <= FULL_RANGE[1])
    checks = {
        "yi_verses": {"sliced": len(yi_records), "source_in_range": src_verses, "match": len(yi_records) == src_verses},
        "full_lines": {"sliced": len(full_records), "source_in_range": src_lines, "match": len(full_records) == src_lines},
    }

    # 节拍覆盖与每拍统计
    beat_stats = []
    for no, name, lo, hi in BEATS:
        v = sum(1 for r in yi_records if no in r["beats"])
        beat_stats.append({"no": no, "name": name, "pages": f"p{lo}-{hi}" if hi > lo else f"p{lo}",
                           "yi_verses": v})
    covered_beats = [b["no"] for b in beat_stats if b["yi_verses"] > 0]

    # 页覆盖：切片范围内意译层 0 句的页；以及无节拍归属的句子
    pages_with_verses = {r["page"] for r in yi_records}
    zero_pages = [p for p in range(YI_RANGE[0], YI_RANGE[1] + 1) if p not in pages_with_verses]
    no_beat_pages = {}
    for r in yi_records:
        if not r["beats"]:
            no_beat_pages[r["page"]] = no_beat_pages.get(r["page"], 0) + 1

    # 抽验：随机 10 句回显指针与文本
    samples = []
    for rec in random.sample(yi_records, min(10, len(yi_records))):
        samples.append({"pointer": rec["pointer"], "yi_text": rec["yi"]["text"],
                        "beats": rec["beats"], "layout": rec["layout"], "flags": rec["flags"]})

    meta = {
        "piece_id": PIECE_ID,
        "piece": PIECE,
        "generated": date.today().isoformat(),
        "source_doc": {"doc_id": "buyi_epic_12204413", "canon_version": "v1.0-ocr", "engine": "vision-python-v1"},
        "pages": {"yi_verses": list(YI_RANGE), "full_lines": list(FULL_RANGE), "tail_qa": list(TAIL_QA_RANGE)},
        "beats": beat_stats,
        "tail_metadata": TAIL_METADATA,
        "tail_garbled_flagged": flagged,
        "counts": {"yi_verses": len(yi_records), "full_lines": len(full_records),
                   "daguan_mubodong_lines": len(daguan_recs), "daguan_hit_pages": hit_pages},
        "pointer_check": {"replayed_ok": ok, "failed": len(bad)},
        "daguan_hits": {"totals": totals, "expected_from_design_doc": DAGUAN_EXPECTED, "diff": diff},
        "missing": [],
    }
    (OUT / "piece_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    write_report(checks, beat_stats, covered_beats, zero_pages, no_beat_pages, ok, bad, samples,
                 totals, diff, len(yi_records), len(full_records), len(daguan_recs), hit_pages, flagged)
    print(json.dumps({"yi": len(yi_records), "full": len(full_records), "pointer_ok": ok,
                      "pointer_bad": len(bad), "beats_covered": f"{len(covered_beats)}/14",
                      "zero_pages": zero_pages, "daguan_totals": totals}, ensure_ascii=False, indent=1))


def write_report(checks, beat_stats, covered_beats, zero_pages, no_beat_pages, ok, bad, samples,
                 totals, diff, n_yi, n_full, n_daguan, n_hit_pages, flagged):
    L = []
    L.append("# S0 切片质检报告 —— 《安王与祖王》")
    L.append("")
    L.append(f"> 日期：{date.today().isoformat()}｜脚本：`scripts/s0_slice.py`（规则代码，固定 seed 抽验）")
    L.append("> 性质声明：本报告为只读检查与切片产出记录，未改动任何语料源文件。")
    L.append("")
    L.append("## 一、总体统计")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 意译层切片句数（p904-985） | {n_yi} |")
    L.append(f"| full 层切片行数（p904-985） | {n_full} |")
    L.append(f"| 节拍覆盖 | {len(covered_beats)}/14 |")
    L.append(f"| 意译层 0 句页 | {zero_pages if zero_pages else '无'} |")
    L.append(f"| 大观《穆播董》切片行数（P121-122） | {n_daguan} |")
    L.append(f"| 大观命中页数（安王/祖王/盘果任一） | {n_hit_pages} |")
    L.append("")
    L.append("### 每节拍意译句数")
    L.append("")
    L.append("| # | 节拍 | 页码 | 意译句数 |")
    L.append("|---|------|------|---------|")
    for b in beat_stats:
        L.append(f"| {b['no']} | {b['name']} | {b['pages']} | {b['yi_verses']} |")
    L.append("")
    L.append("## 二、对账")
    L.append("")
    L.append("| 项 | 切片数 | 源范围内数 | 一致 |")
    L.append("|---|-------|-----------|------|")
    for k, v in checks.items():
        L.append(f"| {k} | {v['sliced']} | {v['source_in_range']} | {'✓' if v['match'] else '✗ 不一致'} |")
    L.append("")
    L.append("## 三、指针回放校验（yi 文本 vs full 层规范行 char 区间）")
    L.append("")
    L.append(f"- 回放一致：**{ok}** / {n_yi}；失败：**{len(bad)}**")
    if bad:
        L.append("")
        L.append("| doc | page | line_no | char区间 | 原因 | 期望 | 回放 |")
        L.append("|-----|------|---------|---------|------|------|------|")
        for b in bad[:30]:
            L.append(f"| {b['doc_id']} | p{b['page']} | L{b['line_no']} | [{b['char_start']},{b['char_end']}) "
                     f"| {b['reason']} | {b.get('expect','')} | {repr(b.get('got',''))} |")
        if len(bad) > 30:
            L.append(f"\n（仅列前 30 条，共 {len(bad)} 条）")
    L.append("")
    L.append("## 四、抽验记录（随机 10 句，seed=42）")
    L.append("")
    for s in samples:
        p = s["pointer"]
        L.append(f"- p{p['page']} L{p['line_no']} [{p['char_start']},{p['char_end']}) 拍{s['beats']} "
                 f"({s['layout']}, flags={s['flags'] or '无'}）：{s['yi_text']}")
    L.append("")
    L.append("## 五、发现")
    L.append("")
    if zero_pages:
        L.append(f"1. 意译层 {len(zero_pages)} 页 0 句：{zero_pages}（对照 full 层确认是版式页/过渡页还是缺失）")
    else:
        L.append("1. 意译层切片范围内无 0 句页。")
    if no_beat_pages:
        detail = "；".join(f"p{p}×{c}句" for p, c in sorted(no_beat_pages.items()))
        L.append(f"2. 无节拍归属句（节拍表页码空档，需人工裁定归属）：{detail}。"
                 f"p914 内容为“盘果二十五岁成鳏夫，安王成孤儿……”，系节拍 2→3 的过渡段，"
                 f"S0 不裁定，建议 S1 人物表时定夺（倾向归节拍 3 引段）。")
    L.append(f"3. 指针回放失败 {len(bad)} 条（cross_split 类 ±1 字误差属已知待人工项，见 flags 文档）。")
    diff_str = "；".join(f"{k}：实际 {v['actual']} vs 方案 {v['expected']}（Δ{v['delta']:+d}）" for k, v in diff.items())
    L.append(f"4. 大观命中总量与方案所记差异——{diff_str}。")
    L.append(f"5. 篇尾元数据已从 full 层 p985 读出并写入 piece_meta.json（{TAIL_METADATA['status']}）；"
             f"疑似乱码自动标记 {len(flagged)} 行，见 qa/tail_p983-989.txt。")
    L.append("")
    L.append("## 六、人工核对入口")
    L.append("")
    L.append("- `qa/tail_p983-989.txt`：篇尾乱码区逐行（预计 10 分钟）——确认四项元数据与乱码行边界。")
    L.append("- 本报告第三节失败表（如有）：cross_split ±1 字项逐条回看。")
    L.append("- `versions/daguan_hit_pages.csv`：高频页抽查（如 p121-122、p148、p332-353）。")
    L.append("")
    L.append("## 七、结论")
    L.append("")
    all_match = all(v["match"] for v in checks.values())
    L.append(f"- 对账{'全部一致' if all_match else '存在不一致（见第二节）'}；节拍覆盖 {len(covered_beats)}/14；"
             f"指针回放 {ok}/{n_yi}。")
    L.append("- S0 产出齐备：主料切片 ×2、节拍标注、大观版本语料 ×3、piece_meta.json、qa 查验件。")
    (OUT / "s0_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
