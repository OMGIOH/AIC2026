// macOS 原生 Vision OCR 批量处理 PDF 指定页范围
// 用法: 先编译, 再运行
//   swiftc -O -framework PDFKit -framework Vision -o ocr_vision ocr_vision.swift
//   ./ocr_vision <pdf路径> <起始页1based> <结束页1based> <输出目录>

import Foundation
import PDFKit
import Vision
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 4 else {
    print("用法: ./ocr_vision <pdf路径> <起始页1based> <结束页1based> <输出目录>")
    exit(1)
}

let pdfPath = args[1]
let startPage = max(1, Int(args[2]) ?? 1)     // 1-based
let endPage = Int(args[3]) ?? startPage
let outDir = args[4]

guard let doc = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
    print("无法打开 PDF: \(pdfPath)")
    exit(1)
}
let totalPages = doc.pageCount
print("PDF 总页数: \(totalPages)，处理范围: 第\(startPage)-第\(min(endPage, totalPages))页")

try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

let fileName = (pdfPath as NSString).lastPathComponent
let baseName = (fileName as NSString).deletingPathExtension
let outPath = "\(outDir)/\(baseName)_p\(startPage)-\(min(endPage, totalPages)).txt"

let recognitionLevel: VNRequestTextRecognitionLevel = .accurate
let languages = ["zh-Hans", "zh-Hant", "en-US"]
let scale: CGFloat = 3.0 // 高分渲染，提升识别率

var combinedLines: [String] = []
let upperBound = min(endPage, totalPages)

for pageNum in startPage...upperBound {
    autoreleasepool {
        guard let page = doc.page(at: pageNum - 1) else { return }
        let bounds = page.bounds(for: .mediaBox)
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
        request.recognitionLevel = recognitionLevel
        request.usesLanguageCorrection = true
        request.recognitionLanguages = languages
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        try? handler.perform([request])

        var pageText = ""
        if let observations = request.results {
            // 自上而下、从左到右排序（Vision 坐标系原点在左下）
            let sorted = observations.sorted { a, b in
                if abs(a.boundingBox.midY - b.boundingBox.midY) > 0.01 {
                    return a.boundingBox.midY > b.boundingBox.midY
                } else {
                    return a.boundingBox.minX < b.boundingBox.minX
                }
            }
            for obs in sorted {
                if let cand = obs.topCandidates(1).first {
                    pageText += cand.string + "\n"
                }
            }
        }
        let pgText = "===== 第\(pageNum)页 =====\n" + pageText
        combinedLines.append(pgText)
    }
    print("完成第 \(pageNum) 页")
}

try? combinedLines.joined(separator: "\n").write(toFile: outPath, atomically: true, encoding: .utf8)
print("已保存: \(outPath)")