"""版式还原：中文层过滤 + 垂直重叠聚类成行 + 行内横坐标排序拼接。

与 Swift 版 ocr_vision_zh 的算法**逐行等价移植**（阈值、排序、拼接规则完全一致），
目的：先在相同页面上验证移植正确性（应产出逐字相同的行），再扩展到全本。

相对 Swift 版的差异只有一处：行/块保留几何坐标，供证据指针升级。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any


def is_mostly_chinese(s: str) -> bool:
    """与 Swift isMostlyChinese 相同：含至少 1 个 CJK 汉字即保留。"""
    for ch in s:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF      # 基本区
                or 0x3400 <= o <= 0x4DBF    # 扩展A（含布依族造字）
                or 0x20000 <= o <= 0x2A6DF  # 扩展B
                or 0xF900 <= o <= 0xFAFF):  # 兼容区
            return True
    return False


@dataclass
class Block:
    text: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    conf: float = 1.0
    bbox_px: Dict[str, int] = field(default_factory=dict)
    bbox_norm: Dict[str, float] = field(default_factory=dict)

    @property
    def mid_y(self) -> float:
        return (self.min_y + self.max_y) / 2

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


def to_blocks(raw_blocks: List[Dict]) -> List[Block]:
    """把引擎输出的原始块转成版式块（只保留中文层）。"""
    out: List[Block] = []
    for b in raw_blocks:
        if not is_mostly_chinese(b["text"]):
            continue
        n = b["bbox_norm"]
        out.append(Block(
            text=b["text"],
            min_x=n["x"], max_x=n["x"] + n["w"],
            min_y=n["y"], max_y=n["y"] + n["h"],
            conf=b.get("conf", 1.0),
            bbox_px=b.get("bbox_px", {}),
            bbox_norm=dict(n),
        ))
    return out


def group_into_bands(blocks: List[Block]) -> List[List[Block]]:
    """按垂直重叠度聚类成视觉行（与 Swift groupIntoBands 等价）。

    规则：块与当前行的垂直重叠 >= 40% 块高 → 同一行。
    Swift: sorted { $0.midY > $1.midY }（自上而下，稳定排序）。
    """
    sorted_blocks = sorted(blocks, key=lambda b: -b.mid_y)
    bands: List[List[Block]] = []
    current: List[Block] = []
    band_top = 0.0
    band_bottom = 0.0
    for b in sorted_blocks:
        if not current:
            current = [b]
            band_top, band_bottom = b.max_y, b.min_y
        else:
            overlap = min(band_top, b.max_y) - max(band_bottom, b.min_y)
            if b.height > 0 and overlap >= 0.4 * b.height:
                current.append(b)
                band_top = max(band_top, b.max_y)
                band_bottom = min(band_bottom, b.min_y)
            else:
                bands.append(current)
                current = [b]
                band_top, band_bottom = b.max_y, b.min_y
    if current:
        bands.append(current)
    return bands


def join_band(band: List[Block]) -> Tuple[str, Dict[str, Any]]:
    """行内按横坐标排序拼接（与 Swift joinBand 等价）。

    规则：间隙 > 1.0 倍平均块高 → 插入全角空格（栏间分隔）。
    返回 (行文本, 行元信息含几何并集)。
    """
    sorted_b = sorted(band, key=lambda b: b.min_x)
    avg_h = sum(b.height for b in band) / len(band)
    parts: List[str] = []
    prev: Block | None = None
    n_gaps = 0
    for b in sorted_b:
        if prev is not None:
            gap = b.min_x - prev.max_x
            if gap > avg_h * 1.0:
                parts.append("　")
                n_gaps += 1
        parts.append(b.text)
        prev = b

    # 行级几何并集（像素坐标，供证据几何定位）
    xs = [b.bbox_px.get("x", 0) for b in band if b.bbox_px]
    ys = [b.bbox_px.get("y", 0) for b in band if b.bbox_px]
    x2s = [b.bbox_px.get("x", 0) + b.bbox_px.get("w", 0) for b in band if b.bbox_px]
    y2s = [b.bbox_px.get("y", 0) + b.bbox_px.get("h", 0) for b in band if b.bbox_px]
    line_px: Dict[str, int] = {}
    if xs:
        line_px = {"x": min(xs), "y": min(ys),
                   "w": max(x2s) - min(xs), "h": max(y2s) - min(ys)}

    meta = {
        "avg_block_h": round(avg_h, 6),
        "n_gaps": n_gaps,
        "n_blocks": len(band),
        "min_conf": round(min(b.conf for b in band), 4),
        "bbox_px": line_px,
        "blocks": [
            {"text": b.text, "conf": b.conf, "bbox_px": b.bbox_px,
             "bbox_norm": b.bbox_norm} for b in sorted_b
        ],
    }
    return "".join(parts), meta


def restore_page(raw_blocks: List[Dict]):
    """一页原始块 → (行文本列表, 行元信息列表)。Swift 主循环的等价实现。"""
    blocks = to_blocks(raw_blocks)
    bands = group_into_bands(blocks)
    lines: List[str] = []
    metas: List[Dict[str, Any]] = []
    for band in bands:
        text, meta = join_band(band)
        lines.append(text)
        metas.append(meta)
    return lines, metas, len(raw_blocks), len(blocks)