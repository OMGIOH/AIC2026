#!/usr/bin/env python3
"""初步 demo 知识图谱构建：合并 S1 实体表 + S2 三轮抽取 → graph.json + 自包含 index.html。

- 节点 = S1 白名单中被 S2 引用的实体 + S2 版本层/cross_doc 边引用的外部版本件（占位节点）。
- 边 = S2 triples.jsonl 全部 47 条（fact/version/cross_doc 三层）。
- 事件 = S2 events.jsonl 14 主节拍 + 3 斗法子事件；属性 = attributes.jsonl。
- 幂等可复跑；HTML 由 template.html 注入数据生成，无外部网络依赖。

用法：python3 demo/build_demo.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S1 = ROOT / "实验流程" / "S1_人物表与译名考订"
S2 = ROOT / "实验流程" / "S2_三轮抽取"
DEMO = Path(__file__).resolve().parent

# 外部版本件规范名（与 S0 bibliography.json / S2 边 note 术语一致）
EXTERNAL_LABELS = {
    "person_anwang_mbd":    ("安王（《穆播董》）", "ExternalFigure"),
    "person_zuwang_mbd":    ("祖王（《穆播董》）", "ExternalFigure"),
    "person_panguo_mbd":    ("盘果（《穆播董》）", "ExternalFigure"),
    "piece_daguan_mubodong":("大观《穆播董》",     "ExternalPiece"),
    "piece_wangmo1994":     ("望谟1994整理本",     "ExternalPiece"),
    "piece_holm_hanvueng":  ("Holm英译本Hanvueng", "ExternalPiece"),
    "piece_hanwen_suowen":  ("罕温/索温异名本",    "ExternalPiece"),
    "piece_nakui1600":      ("望谟纳魁村本",       "ExternalPiece"),
    "piece_ceheng1500":     ("册亨版（1988）",     "ExternalPiece"),
    "piece_zhenfeng_bahao": ("贞丰岜浩《告王》",   "ExternalPiece"),
    "doc_guxiejing":        ("古谢经（专书）",     "ExternalDoc"),
}

def load_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

def trim_ptr(p):
    """证据指针瘦身：demo 只保留页/行/引文。"""
    return {k: p[k] for k in ("page", "line_no", "quote", "line_text") if k in p}

def trim_first_seen(fs):
    if not fs: return None
    if "page" in fs:
        return {k: fs[k] for k in ("page", "line_no", "text") if k in fs}
    return {"external": True, "source": fs.get("source", "")}


def main():
    entities = load_jsonl(S1 / "entities.jsonl")
    triples  = load_jsonl(S2 / "triples.jsonl")
    events   = load_jsonl(S2 / "events.jsonl")
    attrs    = load_jsonl(S2 / "attributes.jsonl")

    # 被 S2 引用的实体集合
    refs = set()
    def add(x):
        if isinstance(x, str): refs.add(x)
        elif isinstance(x, list):
            for y in x: add(y)
    for t in triples: add(t["head"]); add(t["tail"])
    for v in events:
        for k in ("agents", "patients", "located"): add(v.get(k))
    for a in attrs: add(a["entity_id"])
    whitelist_ids = {e["id"] for e in entities}
    dangling = sorted(refs - whitelist_ids)

    nodes, ext_count = [], 0
    for e in entities:
        nodes.append({
            "id": e["id"], "name": e["name"], "type": e["type"], "kind": e.get("kind"),
            "source": e.get("source", "in_corpus"), "in_graph": e["id"] in refs,
            "first_seen": trim_first_seen(e.get("first_seen")),
            "aliases": e.get("aliases") or None,
            "ref_forms": e.get("ref_forms") or None,
            "notes": e.get("notes") or None,
        })
    for ext in dangling:
        if ext not in EXTERNAL_LABELS:
            raise SystemExit(f"未登记的外部引用件：{ext}（请补 EXTERNAL_LABELS）")
        name, typ = EXTERNAL_LABELS[ext]
        ext_count += 1
        nodes.append({
            "id": ext, "name": name, "type": typ, "kind": "外部版本件",
            "source": "external_placeholder", "in_graph": True,
            "first_seen": None, "aliases": None, "ref_forms": None,
            "notes": ["demo 占位节点：仅由 S2 版本层/cross_doc 边引用，未入 S1 白名单；正式登记在 S4 落库处理"],
        })

    edges = [{
        "id": t["triple_id"], "source": t["head"], "target": t["tail"],
        "relation": t["relation"], "layer": t.get("layer", "fact"),
        "symmetric": bool(t.get("symmetric")), "beat_no": t.get("beat_no"),
        "note": t.get("note"), "evidence": [trim_ptr(p) for p in t.get("evidence", [])],
    } for t in triples]

    evs = []
    for v in events:
        item = {k: v[k] for k in ("event_id", "beat_no", "name", "pages") if k in v}
        if v.get("sub_of"):
            item["sub_of"] = v["sub_of"]
            item["threat_desc"] = v.get("threat_desc")
            item["counter_desc"] = v.get("counter_desc")
            item["evidence_threat"] = [trim_ptr(p) for p in v.get("evidence_threat", [])]
            item["evidence_counter"] = [trim_ptr(p) for p in v.get("evidence_counter", [])]
        else:
            for k in ("agents", "patients", "located"):
                if v.get(k): item[k] = v[k]
            item["evidence"] = [trim_ptr(p) for p in v.get("evidence", [])]
        if v.get("parallel"):
            item["parallel"] = v["parallel"]
        item["note"] = v.get("note")
        evs.append(item)

    attributes = []
    for a in attrs:
        item = {k: a[k] for k in ("entity_id", "attr", "value", "note") if k in a}
        item["evidence"] = [trim_ptr(p) for p in a.get("evidence", [])]
        attributes.append(item)

    data = {
        "meta": {
            "title": "《安王与祖王》人物知识图谱 · 初步 Demo",
            "generated": str(date.today()),
            "canon_version": "v1.0-ocr（古歌本 p904–985）",
            "counts": {
                "nodes": len(nodes), "edges": len(edges), "events": len(events),
                "attributes": len(attributes), "whitelist": len(entities),
                "external": ext_count, "in_graph_whitelist": len(refs & whitelist_ids),
            },
            "sources": [
                "实验流程/S1_人物表与译名考订/entities.jsonl",
                "实验流程/S2_三轮抽取/triples.jsonl",
                "实验流程/S2_三轮抽取/events.jsonl",
                "实验流程/S2_三轮抽取/attributes.jsonl",
            ],
            "footer": "初步 Demo（非 S4 正式产出）：数据取自 S0–S2 产出；证据指针仅指向《布依族古歌》本（canon v1.0-ocr），"
                      "外部资料仅作版本链/sameAs；外部版本件为占位节点；边 verified 状态由 S3 回验后更新。"
                      "重新生成：python3 demo/build_demo.py",
        },
        "nodes": nodes, "edges": edges, "events": evs, "attributes": attributes,
    }

    (DEMO / "graph.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    template = (DEMO / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if "__GRAPH_DATA__" not in template:
        raise SystemExit("template.html 缺少 __GRAPH_DATA__ 注入点")
    (DEMO / "index.html").write_text(template.replace("__GRAPH_DATA__", payload), encoding="utf-8")

    c = data["meta"]["counts"]
    print(f"demo 生成完毕：节点 {c['nodes']}（白名单 {c['in_graph_whitelist']} 入图 + 外部占位 {c['external']}，"
          f"未入边 {c['whitelist'] - c['in_graph_whitelist']}）· 边 {c['edges']} · 事件 {c['events']} · 属性 {c['attributes']}")
    print(f"输出：demo/graph.json, demo/index.html")

if __name__ == "__main__":
    main()
