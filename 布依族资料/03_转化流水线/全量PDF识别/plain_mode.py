"""plain 模式：不做版式还原的普通识别。

每个 Vision 识别块自成一行的阅读顺序输出：
- 不过滤、不拼接、不插空格，保留全部识别内容（含拼音/国际音标/数字/页码）
- 块即行，行号 = 排序后序号（与 layout 模式共用同一指针协议）

阅读顺序（三级，2026-09-26 定稿）：
    ① 切栏：双栏页先左栏、后右栏（detect_columns）
    ② 栏内聚类：按垂直重叠把块聚成「视觉行」（cluster_rows）
    ③ 行内排序：视觉行内按 x 左→右；视觉行之间上→下

    跨栏块（居中标题、通栏脚注）不参与左右栏，按 y 插回原位：
    它们把页面切成若干横向带，每带内先左栏后右栏。实例：古谢经 p363
    「穆稳（咒牛经）」在页首、p412「十三 搭桥歌」在页中，若一律追加到页尾，
    标题就跑到了页末。

    聚类只用来决定**排序键**，不合并文本——输出仍是「每块一行」。
    这是与 layout 模式的根本区别：layout 会把一行内的块拼接成单行文本（古歌四行对照需要），
    plain 保留块粒度，以便下游按块定位、按块置信度过滤。

为什么需要 ②（2026-09-26 新增）：
    Vision 对同一视觉行内各块的 y 估计有抖动（实测全册中位 4px），
    若直接按 y 排序，抖动会把行内词序打乱。
    实例：古谢经 p200 直译行应为「欢 蹦眺 下 街」，实际排成「街 / 蹦眺 / 欢 / 下」。

聚类判据（对称重叠，与 layout 的 40% 不同）：
    重叠 >= 60% × max(两块高度) → 同一视觉行。
    layout 用「重叠 >= 40% × 传入块高」，在行距很密的页上会**链式过度合并**
    （实测古谢经 p68 把记音/IPA/直译三行串成 89px 的一行）。
    改成对称 60% 后：p68 降到 15px，全册再无抖动 >40px 的视觉行，
    而正常页完全不受影响（p200 保持 7px、p100 保持 10px）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 栏间空白带的最小宽度：按页宽比例，并设绝对下限（页宽 1544 时约 28px）
GUTTER_MIN_RATIO = 0.018
GUTTER_MIN_PX = 24.0
# 栏线中心允许的横向区间（占页宽比例）：正文双栏的栏线在页面中间，边缘空白不算
# 实测古谢经 266 页真双栏的中心比例全部 <=0.513，误判的目录页 p15 为 0.676
GUTTER_CENTER_RANGE = (0.32, 0.60)
# 每栏最少块数：避免把标题页、居中短行页误判为双栏
GUTTER_MIN_BLOCKS = 6
# 每栏至少有一个「够宽的块」（占页宽比例）：保证两侧都是正文行
# 目录页右列（页码（1）（2）…）最大块仅占页宽 12%，据此排除
GUTTER_WIDE_BLOCK_RATIO = 0.20
# 同一视觉行的重叠判据：重叠 >= 该比例 × 较高块的高度
ROW_OVERLAP_RATIO = 0.60
# 兜底判栏：核心区覆盖度最低处仍低于此比例，才认为栏线只是被跨栏块盖住
# （真双栏实测最大 7.1% 页高；真单栏「通栏正文夹双栏引诗」实测 19.8%~39.0%）
GUTTER_FALLBACK_RATIO = 0.12
# 兜底判栏的「两侧都得有正文长行」比例，比主判据松（主判据 20%，古谢经 p363 左栏
# 最宽块 309px 恰为页宽 20.0%，卡在边界上）
GUTTER_FALLBACK_WIDE_RATIO = 0.18

# —— 判栏的空带扫描参数（2026-09-26 由一维投影改为覆盖度法）——
# x 方向网格步长（像素）：覆盖度统计的分辨率
GUTTER_GRID_PX = 4
# 某网格上「块覆盖高度之和 / 页高」低于此比例视为空白
GUTTER_EMPTY_RATIO = 0.03
# 只在页面中部的这个横向区间内找栏线（页边空白不算）
GUTTER_CORE_RANGE = (0.20, 0.80)
# 参与桥接统计的空带最小宽度（比正式判据的 GUTTER_MIN_PX 松，便于露出窄分隔带）
GUTTER_BAND_MIN_PX = 10.0
# 相邻空带之间只隔着一个噪声块时并成一条：间隙 <= max(8px, 0.5% 页宽)
GUTTER_BRIDGE_MIN_PX = 8.0
GUTTER_BRIDGE_RATIO = 0.005


def coverage_profile(blocks: List[Dict], img_w: int) -> List[float]:
    """每个 x 网格上「全部块覆盖的高度之和」（长度 = ceil(img_w / GUTTER_GRID_PX)）。

    覆盖度统计**必须包含全部块**（页眉/脚注/通栏正文都算）——这是与旧一维投影法的
    根本区别，也是唯一能把下面两类页区分开的依据：
      - 「双栏正文 + 底部全宽脚注」（古谢经 p53）：脚注只贡献约 2% 页高，
        栏线仍是空白带 → 应判双栏；
      - 「单栏正文中夹一段双栏引诗」（文化大观 p108）：通栏正文贡献几十% 页高，
        引诗中间那道缝不是贯通栏线 → 应判单栏。
    """
    step = GUTTER_GRID_PX
    n = (img_w + step - 1) // step
    cover = [0.0] * n
    for b in blocks:
        px = b["bbox_px"]
        i0 = max(0, int(px["x"] // step))
        i1 = min(n - 1, int((px["x"] + px["w"]) // step))
        h = float(px["h"])
        for i in range(i0, i1 + 1):
            cover[i] += h
    return cover


def empty_bands(blocks: List[Dict], img_w: int, img_h: int) -> List[Tuple[float, float]]:
    """页面上全部「整页高度上基本无字」的 x 空带（含页边空白），按 x 升序。

    做法：把页宽切成 GUTTER_GRID_PX 网格，累计每个网格上所有块覆盖的**高度之和**；
    占比 < GUTTER_EMPTY_RATIO 页高即为空白，再桥接只隔一个噪声块的相邻空带。
    """
    step = GUTTER_GRID_PX
    cover = coverage_profile(blocks, img_w)
    n = len(cover)
    limit = GUTTER_EMPTY_RATIO * img_h
    bands: List[Tuple[float, float]] = []
    start: Optional[int] = None
    for i in range(n + 1):
        blank = i < n and cover[i] < limit
        if blank and start is None:
            start = i
        elif not blank and start is not None:
            if (i - start) * step >= GUTTER_BAND_MIN_PX:
                bands.append((float(start * step), float(i * step)))
            start = None

    # 桥接：相邻空带之间只隔一个很小的噪声块时并成一条
    # （实例：古谢经 p122 栏线中段一个 4px 噪声块把栏线切成两条）
    tol = max(GUTTER_BRIDGE_MIN_PX, GUTTER_BRIDGE_RATIO * img_w)
    merged: List[Tuple[float, float]] = []
    for b in bands:
        if merged and b[0] - merged[-1][1] <= tol:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return merged


def gutter_center(blocks: List[Dict], img_w: int, img_h: int,
                  ratio: float) -> Optional[float]:
    """页面核心区内覆盖度最低的 x（栏线中心候选）；最低值超过 ratio × 页高则返回 None。"""
    cover = coverage_profile(blocks, img_w)
    step = GUTTER_GRID_PX
    lo = int(GUTTER_CORE_RANGE[0] * img_w // step)
    hi = min(len(cover), int(GUTTER_CORE_RANGE[1] * img_w // step) + 1)
    if hi <= lo:
        return None
    seg = cover[lo:hi]
    mn = min(seg)
    if mn > ratio * img_h:
        return None
    return float((lo + seg.index(mn)) * step + step / 2.0)


def _detect_columns_primary(blocks: List[Dict], img_w: int,
                            img_h: Optional[int] = None) -> Optional[Tuple[float, float]]:
    """主判据：栏线是「贯通空白带」。返回 (左栏右边界 gl, 右栏左边界 gr)；单栏返回 None。

    做法：用覆盖度法求全部 x 空带（empty_bands），再按五条判定条件筛选。

    五条判定条件（须同时满足）：
      1) 页面中部区间（GUTTER_CORE_RANGE，页宽 20%-80%）内有且只有一条空带
         —— 双栏正文页只有一条贯通栏线；表格/多栏页会有多条列分隔带
         （实测：文化大观 p513 文保单位名录表 2 条、p270 节日一览表 3 条）
         —— 页边空白不计入此数（实例：古谢经 p71 右页边一个孤立「．」噪声块）
      2) 空带宽度 >= max(24px, 1.8% 页宽)
      3) 带中心落在页宽 32%-60%（正文栏线在页面正中）
      4) 左右两侧各 >= 6 个块
      5) 左右两侧各至少有一个宽度 >= 20% 页宽的块（两侧都得是正文行）
         —— 目录页右列只有页码短块，据此被排除（实测：古谢经 p15）

    为什么不是「按块左边界排序 + 维护已见最大右边界」的一维投影（2026-09-26 前的做法）：
    该法隐含假设「没有任何块跨越栏线」，三个实例都栽在这上面——
      - 古谢经 p53：底部全宽脚注（宽 1156px，占页宽 75%）横跨栏线，把栏线整条抹掉；
      - 古谢经 p71：右页边孤立「．」制造出第二条候选带，「有且只有一条」不成立；
      - 古谢经 p122：栏线中段 4px 噪声块把栏线切成两条，同样被否。
    且一维投影只看「有没有块跨过」，不看「跨过的量」；而正确判据是
    「这条带在页面中部、且整页高度上基本无字」。

    实测（2026-09-26 改判据后）：古谢经 430 页中 367 页判为双栏（较旧版 +101，
    回退 0 页）；文化大观 524 页仍为 0 误判；古歌走 layout 模式不受影响。
    另有少数页栏线被跨栏块盖住而看不到空带，由 detect_columns_fallback 兜底。
    """
    if len(blocks) < GUTTER_MIN_BLOCKS * 2:
        return None
    if img_h is None:
        img_h = int(max(b["bbox_px"]["y"] + b["bbox_px"]["h"] for b in blocks)) or 1

    core = [b for b in empty_bands(blocks, img_w, img_h)
            if GUTTER_CORE_RANGE[0] * img_w <= (b[0] + b[1]) / 2.0
            <= GUTTER_CORE_RANGE[1] * img_w]
    if len(core) != 1:                                # 条件 1：中部唯一空带
        return None
    gl, gr = core[0]
    if gr - gl < max(GUTTER_MIN_PX, GUTTER_MIN_RATIO * img_w):
        return None                                   # 条件 2：栏线够宽
    if not (GUTTER_CENTER_RANGE[0] * img_w <= (gl + gr) / 2.0
            <= GUTTER_CENTER_RANGE[1] * img_w):
        return None                                   # 条件 3：栏线在页面正中
    left = [b for b in blocks
            if b["bbox_px"]["x"] + b["bbox_px"]["w"] <= gl + 1]
    right = [b for b in blocks if b["bbox_px"]["x"] >= gr - 1]
    if len(left) < GUTTER_MIN_BLOCKS or len(right) < GUTTER_MIN_BLOCKS:
        return None                                   # 条件 4：两侧都有正文
    wide = GUTTER_WIDE_BLOCK_RATIO * img_w
    if (max(b["bbox_px"]["w"] for b in left) < wide
            or max(b["bbox_px"]["w"] for b in right) < wide):
        return None                                   # 条件 5：两侧都得有正文长行
    return gl, gr


def detect_columns_fallback(blocks: List[Dict], img_w: int,
                            img_h: Optional[int] = None) -> Optional[Tuple[float, float]]:
    """兜底判栏：栏线本身被跨栏块（居中标题、通栏脚注）盖住的页。

    主判据要求栏线在整页高度上基本无字，但一个够高的居中标题就能把它否掉：
    古谢经 p363 的「穆稳（咒牛经）」高 86px（3.8% 页高）、p412 的「桥歌」高 69px（3.1%），
    都超过 GUTTER_EMPTY_RATIO（3%），于是两条真双栏页被判成单栏、
    左右栏按视觉行交错（p363 直译行「男始祖 州 造 死」与右栏「坏 去 潭 去 空」被并成一行）。

    改判据：不看「有没有贯通空白带」，改看「核心区覆盖度最低的 x 处还剩多少覆盖」——
    真双栏页那里只剩跨栏块贡献（p363 7.1%、p412 3.1%），
    真单栏页那里仍有正文（文化大观 p108 通栏正文夹双栏引诗 39.0%、p154 22.9%），
    两者之间（7.1% ~ 19.8%）有干净的空档，故取 GUTTER_FALLBACK_RATIO=12% 为界。

    返回 (x, x)——即栏线中心本身，调用方据此划分左右栏。

    —— 为什么必须按册开关（2026-09-26 逐页看图后补）——
    本判据是弱判据：它只问「页面正中是不是基本无字」，不问「那是不是一条栏线」。
    逐页看渲染图核验后发现，它在双栏册里准、在单栏册里错：
      - 古谢经（本册 367/430 页由主判据判为双栏）：新增 30 页**全部**是真双栏正文，
        栏线被跨栏居中标题（p363「穆稳（咒牛经）」）或通栏脚注（p17 注①②）盖住；
      - 文化大观（本册主判据判出 0 页双栏）：新增 3 页**全部**是假阳性——
        p86 图文混排（文字绕图，绕出的窄栏并非独立正文列）、
        p513 文保单位名录表（核心区 3 条列缝）、p520 两栏名录（名称↔所在地逐行配对）。
    两类页的几何特征高度相似（都在页面正中留下 2%~10% 的残余覆盖），
    没有任何页面级几何判据能把它们分开——差别是语义的（诗歌双栏 vs 表格/绕图）。
    故正确做法是引入**册级先验**：兜底判据只在「本册主体为双栏正文」时成立，
    由 books.py 的 col_fallback 开关控制（依据：主判据在该册判出的双栏页占比）。
    """
    if len(blocks) < GUTTER_MIN_BLOCKS * 2:
        return None
    if img_h is None:
        img_h = int(max(b["bbox_px"]["y"] + b["bbox_px"]["h"] for b in blocks)) or 1

    x = gutter_center(blocks, img_w, img_h, GUTTER_FALLBACK_RATIO)
    if x is None:
        return None
    if not (GUTTER_CENTER_RANGE[0] * img_w <= x <= GUTTER_CENTER_RANGE[1] * img_w):
        return None                                   # 栏线得在页面正中
    left = [b for b in blocks if b["bbox_px"]["x"] + b["bbox_px"]["w"] <= x]
    right = [b for b in blocks if b["bbox_px"]["x"] >= x]
    if len(left) < GUTTER_MIN_BLOCKS or len(right) < GUTTER_MIN_BLOCKS:
        return None                                   # 两侧都有正文
    wide = GUTTER_FALLBACK_WIDE_RATIO * img_w
    if (max(b["bbox_px"]["w"] for b in left) < wide
            or max(b["bbox_px"]["w"] for b in right) < wide):
        return None                                   # 两侧都得有正文长行
    return x, x


def detect_columns(blocks: List[Dict], img_w: int,
                   img_h: Optional[int] = None,
                   allow_fallback: bool = True) -> Optional[Tuple[float, float]]:
    """判栏总入口：先走主判据（贯通空白带），不中再走兜底判据（跨栏块盖住栏线）。

    返回 (左栏右边界 gl, 右栏左边界 gr)；单栏返回 None。
    兜底判据返回 (x, x)，即栏线中心本身。

    allow_fallback：是否启用兜底判据，**应由调用方按册传入**（见 books.py 的
    col_fallback）。兜底判据是「核心区覆盖度最低处 < 12% 页高」这一弱判据，
    它只在「本册主体是双栏正文」的先验下成立；对单栏散文册会误判（见下）。
    """
    cols = _detect_columns_primary(blocks, img_w, img_h)
    if cols:
        return cols
    if not allow_fallback:
        return None
    return detect_columns_fallback(blocks, img_w, img_h)


def gutter_split(blocks: List[Dict], img_w: int,
                 img_h: Optional[int] = None,
                 allow_fallback: bool = True) -> Optional[float]:
    """划分左右栏的 x 分界；单栏返回 None。

    主判据给的是空白带 [gl, gr]，但带内可能压着矮块（矮到仍算「空白」），
    取带中点会把这类块误判成跨栏（实例：古谢经 p19「尾（巴）」x547-691
    压进 676-780 的带内，被判跨栏后追加到页尾，实际它是左栏的注）。
    故统一取**带内覆盖度最低的 x**：空白带内它必落在无字处，矮块不再被误判。
    """
    cols = detect_columns(blocks, img_w, img_h, allow_fallback)
    if not cols:
        return None
    gl, gr = cols
    if gl == gr:
        return gl
    if img_h is None:
        img_h = int(max(b["bbox_px"]["y"] + b["bbox_px"]["h"] for b in blocks)) or 1
    cover = coverage_profile(blocks, img_w)
    step = GUTTER_GRID_PX
    i0 = max(0, int(gl // step))
    i1 = min(len(cover) - 1, int(gr // step))
    if i1 < i0:
        return (gl + gr) / 2.0
    seg = cover[i0:i1 + 1]
    return float((i0 + seg.index(min(seg))) * step + step / 2.0)


def cluster_rows(blocks: List[Dict], ratio: float = ROW_OVERLAP_RATIO) -> List[List[Dict]]:
    """把同一栏内的块聚成视觉行（像素坐标，左上原点）。

    按 y 自上而下扫描，与当前行做垂直重叠判定：
        重叠 >= ratio × max(当前行最高块, 新块) → 并入当前行
    否则另起一行。返回的视觉行天然按上→下排列。

    注意：这是单趟贪心聚类，不是传递闭包——故不会因一个块同时与上下两行重叠
    而把两行串起来（这正是 layout 的 40% 判据在密行页上的失效模式）。
    """
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b["bbox_px"]["y"], b["bbox_px"]["x"]))
    rows: List[List[Dict]] = []
    cur: List[Dict] = []
    top = bottom = 0.0
    max_h = 0.0
    for b in ordered:
        px = b["bbox_px"]
        y1, h = float(px["y"]), float(px["h"])
        y2 = y1 + h
        if not cur:
            cur, top, bottom, max_h = [b], y1, y2, h
            continue
        overlap = min(bottom, y2) - max(top, y1)
        if h > 0 and overlap >= ratio * max(max_h, h):
            cur.append(b)
            top, bottom = min(top, y1), max(bottom, y2)
            max_h = max(max_h, h)
        else:
            rows.append(cur)
            cur, top, bottom, max_h = [b], y1, y2, h
    if cur:
        rows.append(cur)
    return rows


def order_plain(raw_blocks: List[Dict], img_w: Optional[int] = None,
                img_h: Optional[int] = None,
                allow_fallback: bool = True) -> List[List[Dict]]:
    """按阅读顺序返回视觉行（每行是块列表，行内已按 x 左→右）。

    ① 切栏（可选）→ ② 栏内聚类 → ③ 行内按 x。
    双栏页的跨栏块（居中标题、通栏脚注）不归任何一栏，按 y 插回原位：
    它们把页面切成若干横向带，每带内先左栏各行、后右栏各行。
    单栏页或没有跨栏块时退化为「上→下」。

    返回结构保留「视觉行」层次，便于调用方按需拍平（restore_plain）或做行级诊断。
    allow_fallback 透传给判栏，含义见 detect_columns。
    """
    if not raw_blocks:
        return []
    x = gutter_split(raw_blocks, img_w, img_h, allow_fallback) if img_w else None
    if x is None:
        return [sorted(row, key=lambda b: b["bbox_px"]["x"])
                for row in cluster_rows(raw_blocks)]

    left, right, mid = [], [], []
    for b in raw_blocks:
        px = b["bbox_px"]
        if px["x"] + px["w"] <= x:
            left.append(b)
        elif px["x"] >= x:
            right.append(b)
        else:
            mid.append(b)

    def yc(b: Dict) -> float:
        return b["bbox_px"]["y"] + b["bbox_px"]["h"] / 2.0

    def row_yc(row: List[Dict]) -> float:
        return (min(b["bbox_px"]["y"] for b in row)
                + max(b["bbox_px"]["y"] + b["bbox_px"]["h"] for b in row)) / 2.0

    seps = sorted(mid, key=yc)

    def band_of(y: float) -> int:
        return sum(1 for s in seps if yc(s) < y)

    units = []                                  # (带号, 栏号, y, 行)
    for col, bs in ((0, left), (1, right)):
        for row in cluster_rows(bs):
            units.append((band_of(row_yc(row)), col, row_yc(row),
                          sorted(row, key=lambda b: b["bbox_px"]["x"])))

    rows: List[List[Dict]] = []
    for k in range(len(seps) + 1):
        band = sorted((u for u in units if u[0] == k), key=lambda u: (u[1], u[2]))
        rows.extend(u[3] for u in band)
        if k < len(seps):
            rows.append([seps[k]])              # 跨栏块插在两带之间
    return rows


def restore_plain(raw_blocks: List[Dict], img_w: Optional[int] = None,
                  img_h: Optional[int] = None,
                  allow_fallback: bool = True):
    """一页原始块 → (行文本列表, 行元信息列表, 原始块数, 保留块数)。

    与 layout.restore_page 同签名同返回结构，便于 run_full 统一调用。
    每块自成一行（不拼接），行序按 order_plain。
    """
    rows = order_plain(raw_blocks, img_w, img_h, allow_fallback)

    lines: List[str] = []
    metas: List[Dict[str, Any]] = []
    for row in rows:
        for b in row:
            lines.append(b["text"])
            metas.append({
                "n_blocks": 1,
                "min_conf": b["conf"],
                "bbox_px": b["bbox_px"],
                "blocks": [{"text": b["text"], "conf": b["conf"],
                            "bbox_px": b["bbox_px"]}],
            })
    return lines, metas, len(raw_blocks), len(raw_blocks)