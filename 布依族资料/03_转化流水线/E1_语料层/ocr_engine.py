"""OCR 引擎：PDF 页渲染 + Vision 文本识别（Swift ocr_vision_zh 的 Python 等价重写）。

与 Swift 版对齐的参数（不可改动，否则同页对照失去意义）：
- 渲染倍率 3.0，sRGB，先填白底再画页（mediaBox）
- recognitionLevel = accurate（本机框架枚举 Accurate=0）
- usesLanguageCorrection = False（保留古字原貌）
- recognitionLanguages = ["zh-Hans", "zh-Hant"]

相对 Swift 版的升级：每个文本块保留 bbox（归一化 + 像素两级）与 confidence，
供证据指针升级为「字符 + 几何」双坐标。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import Quartz
from Vision import VNImageRequestHandler, VNRecognizeTextRequest

# 本机框架：VNRequestTextRecognitionLevelFast=1, Accurate=0（实测探明）
LEVEL_ACCURATE = 0


def open_pdf(path: str | Path):
    """打开 PDF，返回 PDFKit.PDFDocument（与 Swift 版同一渲染路径）。"""
    from Foundation import NSURL
    url = NSURL.fileURLWithPath_(str(Path(path).resolve()))
    doc = Quartz.PDFDocument.alloc().initWithURL_(url)
    if doc is None:
        raise RuntimeError(f"无法打开 PDF: {path}")
    return doc


def page_count(doc) -> int:
    return int(doc.pageCount())


def render_page(doc, page_1based: int, scale: float = 3.0):
    """渲染单页为 CGImage，返回 (cgimage, width_px, height_px)。

    与 Swift 版逐行对齐：PDFKit 页面绘制（drawWithBox:toContext:）+ 3 倍缩放 + 白底。
    注意必须走 PDFKit 而非 CGContextDrawPDFPage——两者光栅化像素不同，
    Vision 对像素差异敏感（实测会改变读数，见 results/compare_report.md 首版）。
    """
    page = doc.pageAtIndex_(page_1based - 1)
    if page is None:
        raise RuntimeError(f"页不存在: {page_1based}")
    rect = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
    w = max(1, int(rect.size.width * scale))
    h = max(1, int(rect.size.height * scale))

    cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    if cs is None:
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, cs,
                                       Quartz.kCGImageAlphaPremultipliedLast)
    white = Quartz.CGColorCreate(cs, (1.0, 1.0, 1.0, 1.0))
    Quartz.CGContextSetFillColorWithColor(ctx, white)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
    Quartz.CGContextScaleCTM(ctx, scale, scale)
    Quartz.CGContextTranslateCTM(ctx, -rect.origin.x, -rect.origin.y)
    page.drawWithBox_toContext_(Quartz.kPDFDisplayBoxMediaBox, ctx)
    img = Quartz.CGBitmapContextCreateImage(ctx)
    if img is None:
        raise RuntimeError(f"页面渲染失败: 第{page_1based}页")
    return img, w, h


def recognize(cgimage, img_w: int, img_h: int,
              languages: Tuple[str, ...] = ("zh-Hans", "zh-Hant"),
              uses_correction: bool = False) -> List[Dict]:
    """识别单页，返回块列表 [{text, bbox_norm, bbox_px, conf}]。

    bbox_norm：Vision 归一化坐标，原点在左下（与 Swift 一致，供行聚类用）
    bbox_px  ：像素坐标，原点在左上（供渲染图上框选证据用）
    """
    req = VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(LEVEL_ACCURATE)
    req.setUsesLanguageCorrection_(uses_correction)
    req.setRecognitionLanguages_(list(languages))

    handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision 识别失败: {err}")

    blocks: List[Dict] = []
    for obs in req.results():
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        text = cand.string()
        try:
            conf = float(cand.confidence())
        except Exception:  # noqa: BLE001
            conf = 1.0
        bb = obs.boundingBox()
        x, y = float(bb.origin.x), float(bb.origin.y)
        bw, bh = float(bb.size.width), float(bb.size.height)
        blocks.append({
            "text": text,
            "conf": round(conf, 4),
            "bbox_norm": {"x": x, "y": y, "w": bw, "h": bh},
            "bbox_px": {
                "x": round(x * img_w),
                "y": round((1.0 - y - bh) * img_h),   # 左下原点 → 左上原点
                "w": round(bw * img_w),
                "h": round(bh * img_h),
            },
        })
    return blocks


def save_png(cgimage, out_path: str | Path) -> bool:
    """把渲染图存成 PNG（供人工复核框选证据）。失败不致命。"""
    try:
        p = str(out_path).encode("utf-8")
        url = Quartz.CFURLCreateFromFileSystemRepresentation(None, p, len(p), False)
        dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
        if dest is None:
            return False
        Quartz.CGImageDestinationAddImage(dest, cgimage, None)
        return bool(Quartz.CGImageDestinationFinalize(dest))
    except Exception:  # noqa: BLE001
        return False