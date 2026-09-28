// 只识别中文 + 版式还原版：
// 1) OCR 后仅保留中文占比高的文本块（剔除拼音/IPA/纹样/页码数字）
// 2) 按「垂直重叠度」把文本块聚类成视觉行（解决上下标导致的同行散乱）
// 3) 行内按横坐标排序，按间隙智能拼接（大间隙=栏间，加全角空格）
// 用法: ./ocr_vision_zh <pdf路径> <起始页1based> <结束页1based> <输出目录>

import Foundation
import PDFKit
import Vision
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 4 else {
    print("用法: ./ocr_vision_zh <pdf路径> <起始页1based> <结束页1based> <输出目录>")
    exit(1)
}

let pdfPath = args[1]
let startPage = max(1, Int(args[2]) ?? 1)
let endPage = Int(args[3]) ?? startPage
let outDir = args[4]

// 判断是否保留：只要含至少 1 个 CJK 汉字即保留（纯拼音/IPA/数字/纹样行不含汉字，会被剔除）
// 宽松规则可找回被 OCR 带上杂点的单字（如「.朝」「百姓（？）」）
func isMostlyChinese(_ s: String) -> Bool {
    for ch in s.unicodeScalars {
        let isCJK =
            (0x4E00...0x9FFF).contains(ch.value) ||    // 基本区
            (0x3400...0x4DBF).contains(ch.value) ||    // 扩展A（含布依族造字）
            (0x20000...0x2A6DF).contains(ch.value) ||  // 扩展B
            (0xF900...0xFAFF).contains(ch.value)       // 兼容区
        if isCJK { return true }
    }
    return false
}

struct Block {
    let text: String
    let minX: CGFloat, maxX: CGFloat
    let minY: CGFloat, maxY: CGFloat
    var midY: CGFloat { (minY + maxY) / 2 }
}

// 按垂直重叠度把块聚类成视觉行：块与当前行的垂直重叠 >= 40% 块高 → 同一行
func groupIntoBands(_ blocks: [Block]) -> [[Block]] {
    let sorted = blocks.sorted { $0.midY > $1.midY }  // 自上而下
    var bands: [[Block]] = []
    var current: [Block] = []
    var bandTop: CGFloat = 0, bandBottom: CGFloat = 0
    for b in sorted {
        if current.isEmpty {
            current = [b]; bandTop = b.maxY; bandBottom = b.minY
        } else {
            let overlap = min(bandTop, b.maxY) - max(bandBottom, b.minY)
            let bHeight = b.maxY - b.minY
            if bHeight > 0 && overlap >= 0.4 * bHeight {
                current.append(b)
                bandTop = max(bandTop, b.maxY)
                bandBottom = min(bandBottom, b.minY)
            } else {
                bands.append(current)
                current = [b]; bandTop = b.maxY; bandBottom = b.minY
            }
        }
    }
    if !current.isEmpty { bands.append(current) }
    return bands
}

// 行内按横坐标排序，按间隙拼接：间隙 > 1.0×平均块高 → 插全角空格（栏间）
func joinBand(_ band: [Block]) -> String {
    let sorted = band.sorted { $0.minX < $1.minX }
    let avgHeight = band.map { $0.maxY - $0.minY }.reduce(0, +) / CGFloat(band.count)
    var result = ""
    var prev: Block? = nil
    for b in sorted {
        if let p = prev {
            let gap = b.minX - p.maxX
            if gap > avgHeight * 1.0 { result += "　" }
        }
        result += b.text
        prev = b
    }
    return result
}

guard let doc = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
    print("无法打开 PDF: \(pdfPath)")
    exit(1)
}
let totalPages = doc.pageCount
print("PDF 总页数: \(totalPages)，处理范围: 第\(startPage)-第\(min(endPage, totalPages))页")

try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

let fileName = (pdfPath as NSString).lastPathComponent
let baseName = (fileName as NSString).deletingPathExtension
let outPath = "\(outDir)/\(baseName)_p\(startPage)-\(min(endPage, totalPages))_zh.txt"

var combinedLines: [String] = []
let upperBound = min(endPage, totalPages)

for pageNum in startPage...upperBound {
    autoreleasepool {
        guard let page = doc.page(at: pageNum - 1) else { return }
        let bounds = page.bounds(for: .mediaBox)
        let scale: CGFloat = 3.0
        let width = Int(max(1, bounds.width * scale))
        let height = Int(max(1, bounds.height * scale))

        guard let ctx = CGContext(
            data: nil, width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return }

        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        ctx.scaleBy(x: scale, y: scale)
        ctx.translateBy(x: -bounds.origin.x, y: -bounds.origin.y)
        page.draw(with: .mediaBox, to: ctx)
        guard let cgImage = ctx.makeImage() else { return }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false  // 保留古字原貌
        request.recognitionLanguages = ["zh-Hans", "zh-Hant"]
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        try? handler.perform([request])

        // 1) 只收集中文为主的块（带坐标）
        var chineseBlocks: [Block] = []
        if let observations = request.results {
            for obs in observations {
                if let cand = obs.topCandidates(1).first, isMostlyChinese(cand.string) {
                    let bb = obs.boundingBox
                    chineseBlocks.append(Block(
                        text: cand.string,
                        minX: bb.minX, maxX: bb.maxX,
                        minY: bb.minY, maxY: bb.maxY
                    ))
                }
            }
        }

        // 2) 视觉行分带 → 3) 行内排序拼接
        let bands = groupIntoBands(chineseBlocks)
        var pageText = ""
        for band in bands {
            pageText += joinBand(band) + "\n"
        }
        combinedLines.append("===== 第\(pageNum)页 =====\n" + pageText)
    }
    print("完成第 \(pageNum) 页")
}

try? combinedLines.joined(separator: "\n").write(toFile: outPath, atomically: true, encoding: .utf8)
print("已保存: \(outPath)")