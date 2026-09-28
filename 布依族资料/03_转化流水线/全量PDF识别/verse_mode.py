"""古歌 verse 重建：把「记音行 + 直译/意译行」配对成歌句，并切出三层文本。

依据（在 p13-16 试点数据上实测验证）：
1. 意译层是**译文列**上的单个长块：起点 x 稳定在 ~700-740（页内一致），宽 173-198px
2. 直译层是译文列左侧的一串 1-2 字短块（稀疏排布，字距 90-110px）
3. 记音行自成一行，无译文列长块，由 2 个以上短块组成
4. 页边装饰/噪声字起点 x > 950，天然被排除

切分策略（行内）：
- 先按页检测「译文列」位置（候选长块 x 的中位数；候选不足则该页无 verse）
- 每行按块起点 x 相对译文列二分：左侧 → 直译，右侧（含容差）→ 意译
- 字符区间通过与 join_band 完全相同的拼接规则重放计算，
  保证 verse 各层文本是**规范行的子串**，E0 证据坐标（page/line/char）直接可用

配对策略（行间）：
- 「直译/意译行」（含译文列长块者）向前找最近的未认领「记音行」
  （>=2 个短块、最右块 x < 900）
- 找不到 → 该 verse 无音译层，记 flags=["no_yin"]
- 页面判定：译文列候选 < 2 或 gloss 行 < 2 → 整页非歌体（提要/校注），不产 verse
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

TR_X_MIN = 500          # 译文列候选块起点下限（px）
TR_X_MAX = 900          # 上限（页边噪声 x>950 被排除）
TR_W_MIN = 100          # 译文列候选块最小宽度（px）：直译短块 <=77，意译 >=173
TR_TOL = 30             # 直译/意译切分的译文列容差（px）
TR_BAND = 90            # 译文列带宽：col-30 ~ col+90 之内算意译，更右侧算页边噪声
CROSS_X_MIN = 500        # 跨列合并块起点下限：真跨列块起点在直译词区（x~550-660），
                         # 页脚脚注从 x~230 起，会被此条件排除
MARGINAL_X = 850         # 页边装饰/噪声块起点阈值（px）：核心内容判定用
YIN_MAX_BLOCKS_X = 900  # 兼容保留：整体右边界约束（页边噪声 x>950 被排除）
YIN_TEXT_MAX = 12       # 记音行候选：含合并块时全行文本长度上限（直译行意译俱全者不会进入此分支）
YIN_CJK_MIN = 0.5       # 记音行候选：CJK 字符占比下限（排除音标/页码噪声行）
GLOSS_LINE_MIN = 2      # 页内至少要有几条 gloss 行才认为是歌体页
YIN_LINE_MIN = 2       # 记音行候选：短块形态时至少几个块
LOOKBACK = 3           # gloss 行向前找记音行最多回看几行
LAYOUT_YIN_MIN = 2     # 稀疏行 >= 此数判为四行对照正体；否则若译文列存在则判为选译体


def _is_cjk_char(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x20000 <= o <= 0x2A6DF or 0xF900 <= o <= 0xFAFF)


def _cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for c in s if _is_cjk_char(c)) / len(s)


_PUNCT_END = set("，。？！；：、,.?!;:")


def _looks_like_yi(text: str) -> bool:
    """意译块的文本特征：句末有标点，或长度 >=5（如「都城十二座，」）。

    用于排除恰落在译文列上的记音宽块（如 p26「音香」，宽 170px 但仅 2 字无标点）。
    """
    if not text:
        return False
    return text[-1] in _PUNCT_END or len(text) >= 5


def _split_at_col(text: str, bx: float, bw: float, col_x: float) -> int:
    """跨列合并块内部按列位置切分：返回意译起始字符下标。

    按字符宽估算（CJK 记 1 单位、ASCII 记 0.5），取离译文列最近的字符边界。
    误差可能 ±1 字，调用方必须打 cross_split 标记进人工校对队列。
    """
    units = [1.0 if _is_cjk_char(c) else 0.5 for c in text]
    total = sum(units)
    if total <= 0 or bw <= 0:
        return 0
    per = bw / total
    pos = float(bx)
    best_i, best_d = 0, abs(pos - col_x)
    for i in range(len(text)):
        pos += units[i] * per
        d = abs(pos - col_x)
        if d < best_d:
            best_d, best_i = d, i + 1
    return best_i


def _blocks_sorted(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(meta["blocks"], key=lambda b: b["bbox_px"]["x"])


def _char_spans(meta: Dict[str, Any], line_text: str) -> List[Tuple[int, int]]:
    """重放 join_band 的拼接规则，返回每个块在行文本中的 [start, end)。

    规则必须与 layout.join_band 完全一致：间隙 > 1.0 倍平均块高（归一化）→ 插全角空格。
    """
    blocks = _blocks_sorted(meta)
    avg_h = meta.get("avg_block_h", 0.0)
    spans: List[Tuple[int, int]] = []
    cursor = 0
    prev: Optional[Dict[str, Any]] = None
    for b in blocks:
        if prev is not None:
            n, p = b["bbox_norm"], prev["bbox_norm"]
            gap = n["x"] - (p["x"] + p["w"])
            if avg_h > 0 and gap > avg_h * 1.0:
                cursor += len("　")
        start = cursor
        cursor += len(b["text"])
        spans.append((start, cursor))
        prev = b
    return spans


def detect_translation_column(metas: List[Dict[str, Any]]) -> Optional[float]:
    """页级译文列检测：返回译文列起点 x（px），检测不到返回 None。"""
    xs: List[float] = []
    for meta in metas:
        for b in meta["blocks"]:
            bx, bw = b["bbox_px"]["x"], b["bbox_px"]["w"]
            if TR_W_MIN <= bw and TR_X_MIN <= bx <= TR_X_MAX:
                xs.append(float(bx))
    if len(xs) < 2:
        return None
    return statistics.median(xs)


def is_gloss_line(meta: Dict[str, Any], col_x: float) -> bool:
    """直译/意译行判定，两类块形态：

    1. 译文列带内长块：宽 >=90、|x-col|<=60、文本像意译（句末标点或 >=5 字）
    2. 跨列合并块：OCR 把最后的直译词和意译粘成一块（x 在列左、右边界越过列），
       文本同样须像意译。此类块在切分阶段会被按列位置拆开并打 cross_split 标。
    """
    for b in meta["blocks"]:
        bx, bw = b["bbox_px"]["x"], b["bbox_px"]["w"]
        if bw < 90 or not _looks_like_yi(b["text"]):
            continue
        if abs(bx - col_x) <= 60:
            return True
        if (bx >= CROSS_X_MIN and bx < col_x - TR_TOL
                and bx + bw >= col_x + 20):
            return True
    return False


def is_yin_candidate(meta: Dict[str, Any], col_x: float) -> bool:
    """记音行候选。

    形态一（短块序列）：>=2 个窄块（宽 <100），全部不越页右边。
    形态二（OCR 合并块）：行内含宽块，但全行文本短（<=12 字）且 CJK 占比 >=0.5。
    形态二用于放行「江八刀朝」「柔否讧君你」这类被 OCR 合并的记音行，
    同时排除音标行（sau3ivay化2，CJK 占比 0.1）与页码/噪声行（一4-，占比 0.33）。

    gloss 行自身不会进入候选：其意译块（宽 173-198）会触发形态二判定，
    但全行文本长度（直译+意译）远超 12 字。
    """
    blocks = meta["blocks"]
    # 核心块 = 起点在页边装饰区（x >= 850）左侧的块；
    # 记音行常带页边装饰字（驸遊/王醫/陈香），只看核心内容即可放行整行
    core = [b for b in blocks if b["bbox_px"]["x"] < MARGINAL_X]
    if not core:
        return False
    text_all = "".join(b["text"] for b in core)
    big = [b for b in core if b["bbox_px"]["w"] >= TR_W_MIN]
    if big:
        return len(text_all) <= YIN_TEXT_MAX and _cjk_ratio(text_all) >= YIN_CJK_MIN
    return len(core) >= YIN_LINE_MIN


def build_verses(book: Dict[str, Any], page: int,
                 lines: List[str], metas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """一页 → verse 记录列表。非歌体页返回空列表。

    版式判定：
    - interlinear（四行对照正体）：页内有稀疏记音行 → 记音/直译/意译三层配对
    - xuanyi（两行对照选译体）  ：无记音行但译文列存在 → 直译/意译两层，
      音译层合法缺席（不是失败）。选译体每条 gloss 行自成一个 verse。
    """
    col_x = detect_translation_column(metas)
    if col_x is None:
        return []

    gloss_idx = [i for i, m in enumerate(metas) if is_gloss_line(m, col_x)]
    if len(gloss_idx) < GLOSS_LINE_MIN:
        return []

    # gloss 行（直译+意译俱全者）永不作为稀疏行候选：
    # 短 gloss 行全行 <=12 字且 CJK 占比高，会被合并块形态误放行
    yin_flags = [is_yin_candidate(m, col_x) and not is_gloss_line(m, col_x)
                 for m in metas]
    interlinear = sum(yin_flags) >= LAYOUT_YIN_MIN

    claimed: set[int] = set()
    verses: List[Dict[str, Any]] = []

    for gi in gloss_idx:
        flags: List[str] = []
        yin_i: Optional[int] = None
        if interlinear:
            # 正体：向前找最近的未认领记音行
            for j in range(gi - 1, max(-1, gi - 1 - LOOKBACK), -1):
                if j in claimed or not yin_flags[j]:
                    continue
                yin_i = j
                break
            if yin_i is None:
                flags.append("no_yin")
            else:
                claimed.add(yin_i)
        # 选译体：无记音层，不配对也不打 no_yin（合法缺席）

        # 行内切分：直译（列左）/ 意译（列带内）/ 跨列合并块（拆开）/ 页边噪声（剔除并打标）
        meta = metas[gi]
        spans = _char_spans(meta, lines[gi])
        line_text = lines[gi]

        zhi_parts: List[Tuple[int, int]] = []
        yi_parts: List[Tuple[int, int]] = []
        margin_blocks: List[str] = []
        cross_blocks: List[str] = []
        for b, (s, e) in zip(_blocks_sorted(meta), spans):
            bx, bw, bt = b["bbox_px"]["x"], b["bbox_px"]["w"], b["text"]
            is_cross = (bx >= CROSS_X_MIN and bx < col_x - TR_TOL
                        and bx + bw >= col_x + 20
                        and bw >= 90 and _looks_like_yi(bt))
            if is_cross:
                # 跨列合并块：按列位置把直译尾词和意译拆开（±1 字误差，打标待人工）
                k = _split_at_col(bt, bx, bw, col_x)
                if 0 < k < len(bt):
                    cross_blocks.append(bt)
                    zhi_parts.append((s, s + k))
                    yi_parts.append((s + k, e))
                    continue
                # 切分点退化（贴着块首/块尾）时整块归直译，打标
                cross_blocks.append(bt)
                zhi_parts.append((s, e))
            elif bx < col_x - TR_TOL:
                zhi_parts.append((s, e))
            elif bx <= col_x + TR_BAND:
                yi_parts.append((s, e))
            else:
                margin_blocks.append(b["text"])

        if margin_blocks:
            flags.append("margin_noise")
        if cross_blocks:
            flags.append("cross_split")

        def _slice(parts: List[Tuple[int, int]]) -> Optional[Dict[str, Any]]:
            if not parts:
                return None
            s, e = parts[0][0], parts[-1][1]
            return {
                "text": line_text[s:e],
                "line_no": gi + 1,
                "char_start": s,
                "char_end": e,
            }

        verse: Dict[str, Any] = {
            "type": "verse",
            "doc_id": book["doc_id"],
            "page": page,
            "verse_no": len(verses) + 1,
            "layout": "interlinear" if interlinear else "xuanyi",
            "yi": _slice(yi_parts),
            "zhi": _slice(zhi_parts),
            "flags": flags,
            "cross_blocks": cross_blocks or None,
            "_gi": gi,
            "canon_version": book["canon_version"],
            "engine": book.get("_engine", "vision-python-v1"),
        }
        if yin_i is not None:
            verse["yin"] = {
                "text": lines[yin_i],
                "line_no": yin_i + 1,
                "char_start": 0,
                "char_end": len(lines[yin_i]),
            }
            verse["yin_line_consumed"] = True
        else:
            verse["yin"] = None
        verses.append(verse)

    if not interlinear:
        verses = _postprocess_xuanyi(verses, lines, yin_flags, claimed)

    for k, v in enumerate(verses, 1):
        v["verse_no"] = k
        v.pop("_gi", None)
    return verses


def _postprocess_xuanyi(verses: List[Dict[str, Any]], lines: List[str],
                       yin_flags: List[bool], claimed: set[int]) -> List[Dict[str, Any]]:
    """选译体的两个微版式修正：

    1. 校注顶在意译位：谜语段中，直译行右列常是校注（如「（2.弯）」，句末无标点），
       真意译独占下一行（如「既香又硬朗（2. 弯曲），」）。检测「意译无句末标点 +
       下一个 verse 仅意译」→ 合并：校注降级为 annotation 字段，取下一行的意译。
    2. 仅意译（无直译）：向前找最近的未认领稀疏行作直译行（稀疏行检测与记音行
       共用同一套规则——两者形态相同：短块/合并短行）。
    """
    # 1) 校注合并
    merged: List[Dict[str, Any]] = []
    i = 0
    while i < len(verses):
        v = verses[i]
        nxt = verses[i + 1] if i + 1 < len(verses) else None
        yi_text = v["yi"]["text"] if v.get("yi") else ""
        if (yi_text and yi_text[-1] not in _PUNCT_END
                and nxt and nxt.get("yi") and not nxt.get("zhi")):
            v["annotation"] = v["yi"]
            v["yi"] = nxt["yi"]
            v["flags"].append("ann_merged")
            merged.append(v)
            i += 2
            continue
        if yi_text and yi_text[-1] not in _PUNCT_END:
            v["flags"].append("yi_suspect")
        merged.append(v)
        i += 1

    # 2) 仅意译 → 向前配直译行
    for v in merged:
        if not v.get("yi") or v.get("zhi") or "annotation" in v:
            continue
        gi = v.get("_gi", 0)
        for j in range(gi - 1, max(-1, gi - 1 - LOOKBACK), -1):
            if j in claimed or not yin_flags[j]:
                continue
            claimed.add(j)
            v["zhi"] = {
                "text": lines[j],
                "line_no": j + 1,
                "char_start": 0,
                "char_end": len(lines[j]),
            }
            break
        else:
            v["flags"].append("no_zhi")

    return merged
