// 渲染 PDF 指定页为 PNG（用于查看原书版式）
// 用法: ./render <pdf路径> <页码1based> <输出png路径> [dpi]

import Foundation
import PDFKit
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 4 else {
    print("用法: ./render <pdf路径> <页码1based> <输出png路径> [dpi]")
    exit(1)
}

guard let doc = PDFDocument(url: URL(fileURLWithPath: args[1])),
      let page = doc.page(at: Int(args[2])! - 1) else {
    print("无法打开 PDF 或页码越界")
    exit(1)
}

let bounds = page.bounds(for: .mediaBox)
let dpi = Double(args.count >= 5 ? args[3+1] : "150") ?? 150
let scale = CGFloat(dpi / 72.0)
let width = Int(bounds.width * scale)
let height = Int(bounds.height * scale)

let ctx = CGContext(
    data: nil, width: width, height: height,
    bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpace(name: CGColorSpace.sRGB)!,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
)!
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
ctx.scaleBy(x: scale, y: scale)
page.draw(with: .mediaBox, to: ctx)
let img = ctx.makeImage()!

let dest = CGImageDestinationCreateWithURL(
    URL(fileURLWithPath: args[3]) as CFURL,
    "public.png" as CFString, 1, nil
)!
CGImageDestinationAddImage(dest, img, nil)
CGImageDestinationFinalize(dest)
print("已保存: \(args[3])")